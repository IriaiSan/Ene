"""Health monitoring — checks system health and triggers alerts.

Runs periodic health checks and sends alerts to Dad via DM when
something needs attention. Also generates daily/weekly summaries.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Awaitable, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.ene.observatory.store import MetricsStore


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


@dataclass
class HealthAlert:
    """A health check result that may trigger an alert."""
    check_name: str
    severity: Severity
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


@dataclass
class HealthStatus:
    """Overall health status snapshot."""
    status: str  # "healthy" | "warning" | "critical"
    checks: list[dict[str, Any]] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())


class HealthMonitor:
    """Monitors system health and generates alerts.

    Checks are run periodically. When a check fails, an alert callback
    is invoked (typically sending a DM to Dad).
    """

    def __init__(
        self,
        store: "MetricsStore",
        alert_callback: Callable[[HealthAlert], Awaitable[None]] | None = None,
        *,
        cost_spike_multiplier: float = 2.0,
        error_rate_threshold: float = 0.10,
        latency_p95_threshold: int = 30_000,
        no_activity_minutes: int = 30,
    ):
        self._store = store
        self._alert_callback = alert_callback
        self._cost_spike_multiplier = cost_spike_multiplier
        self._error_rate_threshold = error_rate_threshold
        self._latency_p95_threshold = latency_p95_threshold
        self._no_activity_minutes = no_activity_minutes

        # Cooldowns — don't spam the same alert
        self._last_alerts: dict[str, datetime] = {}
        self._alert_cooldown = timedelta(minutes=30)

    async def run_all_checks(self) -> list[HealthAlert]:
        """Run all health checks and return any triggered alerts."""
        alerts: list[HealthAlert] = []

        checks = [
            self._check_error_rate,
            self._check_cost_spike,
            self._check_latency,
            self._check_activity,
        ]

        for check in checks:
            try:
                alert = check()
                if alert and self._should_alert(alert.check_name):
                    alerts.append(alert)
                    self._last_alerts[alert.check_name] = datetime.now()
                    if self._alert_callback:
                        try:
                            await self._alert_callback(alert)
                        except Exception as e:
                            logger.warning(f"Alert callback failed: {e}")
            except Exception as e:
                logger.warning(f"Health check failed: {e}")

        return alerts

    def get_health_status(self) -> HealthStatus:
        """Get current health status without triggering alerts."""
        checks = []
        worst_severity = "healthy"

        # Error rate
        error_data = self._store.get_error_rate(1)
        error_ok = error_data["error_rate"] < self._error_rate_threshold
        checks.append({
            "name": "error_rate",
            "status": "ok" if error_ok else "warning",
            "value": f"{error_data['error_rate']:.1%}",
            "threshold": f"{self._error_rate_threshold:.0%}",
            "details": error_data,
        })
        if not error_ok:
            worst_severity = "warning"

        # Cost spike
        today_cost = self._store.get_total_cost(days=1)
        avg_cost = self._store.get_average_daily_cost(7)
        cost_ok = avg_cost == 0 or today_cost <= avg_cost * self._cost_spike_multiplier
        checks.append({
            "name": "cost_spike",
            "status": "ok" if cost_ok else "warning",
            "value": f"${today_cost:.4f}",
            "threshold": f"${avg_cost * self._cost_spike_multiplier:.4f}",
            "details": {"today": today_cost, "avg_7d": avg_cost},
        })
        if not cost_ok:
            worst_severity = "warning"

        # Latency
        lat_data = self._store.get_latency_percentiles(1)
        latency_ok = lat_data["count"] == 0 or lat_data["p95"] < self._latency_p95_threshold
        checks.append({
            "name": "latency_p95",
            "status": "ok" if latency_ok else "warning",
            "value": f"{lat_data['p95']}ms",
            "threshold": f"{self._latency_p95_threshold}ms",
            "details": lat_data,
        })
        if not latency_ok:
            worst_severity = "warning"

        # Activity
        last_ts = self._store.get_last_call_timestamp()
        if last_ts:
            try:
                last_dt = datetime.fromisoformat(last_ts)
                idle_min = (datetime.now() - last_dt).total_seconds() / 60
                activity_ok = idle_min < self._no_activity_minutes
            except (ValueError, TypeError):
                activity_ok = True
                idle_min = 0
        else:
            activity_ok = True
            idle_min = 0

        checks.append({
            "name": "activity",
            "status": "ok" if activity_ok else "info",
            "value": f"{idle_min:.0f} min idle",
            "threshold": f"{self._no_activity_minutes} min",
        })

        # Error rate critical overrides
        if error_data["error_rate"] > 0.25:
            worst_severity = "critical"

        return HealthStatus(status=worst_severity, checks=checks)

    def _should_alert(self, check_name: str) -> bool:
        """Check if enough time has passed since the last alert for this check."""
        last = self._last_alerts.get(check_name)
        if not last:
            return True
        return datetime.now() - last > self._alert_cooldown

    def _check_error_rate(self) -> HealthAlert | None:
        """Check if error rate is above threshold."""
        data = self._store.get_error_rate(1)  # Last hour
        if data["total_calls"] < 5:
            return None  # Not enough data

        if data["error_rate"] > self._error_rate_threshold:
            severity = Severity.CRITICAL if data["error_rate"] > 0.25 else Severity.WARNING
            return HealthAlert(
                check_name="error_rate",
                severity=severity,
                message=(
                    f"High error rate: {data['error_rate']:.0%} "
                    f"({data['error_count']}/{data['total_calls']} calls in last hour)"
                ),
                details=data,
            )
        return None

    def _check_cost_spike(self) -> HealthAlert | None:
        """Check if today's cost is abnormally high."""
        today_cost = self._store.get_total_cost(days=1)
        avg_cost = self._store.get_average_daily_cost(7)

        if avg_cost == 0 or today_cost < 0.01:
            return None  # No baseline or negligible cost

        if today_cost > avg_cost * self._cost_spike_multiplier:
            return HealthAlert(
                check_name="cost_spike",
                severity=Severity.WARNING,
                message=(
                    f"Cost spike: ${today_cost:.4f} today "
                    f"(avg ${avg_cost:.4f}/day, {today_cost/avg_cost:.1f}x)"
                ),
                details={"today": today_cost, "avg_7d": avg_cost},
            )
        return None

    def _check_latency(self) -> HealthAlert | None:
        """Check if p95 latency is too high."""
        data = self._store.get_latency_percentiles(1)
        if data["count"] < 5:
            return None

        if data["p95"] > self._latency_p95_threshold:
            return HealthAlert(
                check_name="latency",
                severity=Severity.WARNING,
                message=(
                    f"High latency: p95={data['p95']}ms, p50={data['p50']}ms "
                    f"(threshold: {self._latency_p95_threshold}ms)"
                ),
                details=data,
            )
        return None

    def _check_activity(self) -> HealthAlert | None:
        """Check if there's been no activity for a while."""
        last_ts = self._store.get_last_call_timestamp()
        if not last_ts:
            return None

        try:
            last_dt = datetime.fromisoformat(last_ts)
            idle_min = (datetime.now() - last_dt).total_seconds() / 60

            if idle_min > self._no_activity_minutes:
                return HealthAlert(
                    check_name="no_activity",
                    severity=Severity.INFO,
                    message=f"No activity for {idle_min:.0f} minutes",
                    details={"idle_minutes": idle_min},
                )
        except (ValueError, TypeError):
            pass

        return None
