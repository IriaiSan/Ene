"""Observatory — Ene's metrics, monitoring, and experimentation system.

Tracks every LLM call, calculates costs, monitors health, runs A/B
experiments, and serves a real-time web dashboard. Think of it as
Ene's awareness of her own resource usage and performance.

Architecture:
    MetricsCollector → MetricsStore (SQLite)
                     → HealthMonitor → Alerts (via MessageBus)
                     → ReportGenerator → Daily/Weekly DMs
                     → ExperimentEngine (A/B testing)
                     → Dashboard (aiohttp web UI)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.ene import EneModule
from nanobot.ene.observatory.collector import MetricsCollector
from nanobot.ene.observatory.health import HealthAlert, HealthMonitor, Severity
from nanobot.ene.observatory.reporter import ReportGenerator
from nanobot.ene.observatory.store import MetricsStore

if TYPE_CHECKING:
    from nanobot.agent.tools.base import Tool
    from nanobot.bus.events import OutboundMessage
    from nanobot.bus.queue import MessageBus
    from nanobot.ene import EneContext


# Dad's DM channels — alerts go here
DAD_DM_CHANNELS = {
    "discord": "1175414972482846813",
    "telegram": "8559611823",
}


class ObservatoryModule(EneModule):
    """Ene module for metrics collection and monitoring.

    Initialized by the ModuleRegistry on startup. Provides:
    - MetricsCollector for recording LLM calls
    - MetricsStore for querying historical data
    - HealthMonitor for health checks and alerts
    - ReportGenerator for daily/weekly summaries
    """

    def __init__(self) -> None:
        self._store: MetricsStore | None = None
        self._collector: MetricsCollector | None = None
        self._health: HealthMonitor | None = None
        self._reporter: ReportGenerator | None = None
        self._bus: "MessageBus | None" = None
        self._dashboard = None  # DashboardServer, set in initialize
        self._daily_report_hour: int = 8

    @property
    def name(self) -> str:
        return "observatory"

    @property
    def store(self) -> MetricsStore | None:
        """Access the metrics store (for queries)."""
        return self._store

    @property
    def collector(self) -> MetricsCollector | None:
        """Access the metrics collector (for recording calls)."""
        return self._collector

    @property
    def health(self) -> HealthMonitor | None:
        """Access the health monitor."""
        return self._health

    @property
    def reporter(self) -> ReportGenerator | None:
        """Access the report generator."""
        return self._reporter

    async def initialize(self, ctx: EneContext) -> None:
        """Set up the observatory store, collector, health monitor, and reporter."""
        self._bus = ctx.bus

        # Get config
        obs_config = getattr(
            getattr(ctx.config, "agents", None),
            "defaults", None
        )
        if obs_config:
            obs_config = getattr(obs_config, "observatory", None)

        enabled = True
        db_path_str = ""
        if obs_config:
            enabled = getattr(obs_config, "enabled", True)
            db_path_str = getattr(obs_config, "db_path", "")
            self._daily_report_hour = getattr(obs_config, "daily_report_hour", 8)

        if not enabled:
            logger.info("Observatory disabled in config")
            return

        # Determine DB path
        if db_path_str:
            db_path = Path(db_path_str)
        else:
            db_path = ctx.workspace / "observatory.db"

        # Ensure parent directory exists
        db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize store and collector
        self._store = MetricsStore(db_path)
        self._collector = MetricsCollector(self._store)

        # Initialize health monitor with alert callback
        health_kwargs = {}
        if obs_config:
            for attr in ("cost_spike_multiplier", "error_rate_threshold", "latency_p95_threshold"):
                val = getattr(obs_config, attr, None)
                if val is not None:
                    health_kwargs[attr] = val

        self._health = HealthMonitor(
            self._store,
            alert_callback=self._send_alert,
            **health_kwargs,
        )

        # Initialize reporter
        self._reporter = ReportGenerator(self._store)

        # Start dashboard server
        dashboard_enabled = getattr(obs_config, "dashboard_enabled", True) if obs_config else True
        dashboard_port = getattr(obs_config, "dashboard_port", 18791) if obs_config else 18791
        if dashboard_enabled:
            try:
                from nanobot.ene.observatory.dashboard.server import DashboardServer
                self._dashboard = DashboardServer(
                    self._store, self._health, self._reporter,
                    host="127.0.0.1", port=dashboard_port,
                )
                await self._dashboard.start()
            except Exception as e:
                logger.warning(f"Dashboard server failed to start: {e}")
                self._dashboard = None

        self._live_tracer = None  # Set by AgentLoop after init
        logger.info(f"Observatory initialized: {db_path}")

    def set_live_tracer(self, tracer: "Any") -> None:
        """Attach a LiveTracer for the real-time processing dashboard.

        Called by AgentLoop._initialize_ene_modules() after module init.
        """
        self._live_tracer = tracer
        if self._dashboard:
            self._dashboard.set_live_tracer(tracer)
            logger.debug("Wired LiveTracer → dashboard")

    def set_reset_callback(self, cb: "Any") -> None:
        """Attach the agent loop hard-reset callback to the dashboard.

        Called by AgentLoop._initialize_ene_modules() after module init.
        The callback is invoked when the dashboard Hard Reset button is clicked.
        """
        if self._dashboard:
            self._dashboard.set_reset_callback(cb)
            logger.debug("Wired hard_reset callback → dashboard")

    async def _send_alert(self, alert: HealthAlert) -> None:
        """Send a health alert to Dad via DM."""
        if not self._bus:
            return

        from nanobot.bus.events import OutboundMessage

        # Format alert message
        severity_emoji = {
            Severity.CRITICAL: "\U0001f6a8",  # 🚨
            Severity.WARNING: "\u26a0\ufe0f",  # ⚠️
            Severity.INFO: "\u2139\ufe0f",  # ℹ️
        }
        emoji = severity_emoji.get(alert.severity, "")
        msg_text = f"{emoji} {alert.severity.value.upper()}: {alert.message}"

        # Try Discord first, then Telegram
        for channel, chat_id in DAD_DM_CHANNELS.items():
            try:
                await self._bus.publish_outbound(OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=msg_text,
                ))
                logger.debug(f"Alert sent via {channel}: {alert.check_name}")
                return  # Sent successfully
            except Exception as e:
                logger.debug(f"Failed to send alert via {channel}: {e}")

        logger.warning(f"Could not deliver alert: {alert.check_name}")

    async def _send_report(self, report_text: str) -> None:
        """Send a report to Dad via DM."""
        if not self._bus:
            return

        from nanobot.bus.events import OutboundMessage

        for channel, chat_id in DAD_DM_CHANNELS.items():
            try:
                await self._bus.publish_outbound(OutboundMessage(
                    channel=channel,
                    chat_id=chat_id,
                    content=report_text,
                ))
                return
            except Exception as e:
                logger.debug(f"Failed to send report via {channel}: {e}")

    def get_tools(self) -> list["Tool"]:
        """Observatory tools — view_metrics, view_experiments (restricted to Dad)."""
        if not self._store or not self._reporter:
            return []

        from nanobot.ene.observatory.tools import ViewMetricsTool, ViewExperimentsTool

        return [
            ViewMetricsTool(self._store, self._reporter),
            ViewExperimentsTool(self._store),
        ]

    def get_context_block(self) -> str | None:
        """No static context needed for observatory."""
        return None

    async def on_daily(self) -> None:
        """Daily maintenance — health check, daily report, vacuum DB."""
        if not self._store:
            return

        # Run health checks
        if self._health:
            try:
                alerts = await self._health.run_all_checks()
                if alerts:
                    logger.info(f"Observatory health: {len(alerts)} alerts triggered")
            except Exception as e:
                logger.warning(f"Health check failed: {e}")

        # Send daily report
        if self._reporter:
            try:
                report = self._reporter.daily_report()
                await self._send_report(report)
                logger.debug("Daily report sent")
            except Exception as e:
                logger.warning(f"Daily report failed: {e}")

        # Vacuum DB
        try:
            self._store.vacuum()
            logger.debug("Observatory: daily vacuum complete")
        except Exception as e:
            logger.warning(f"Observatory vacuum failed: {e}")

    async def shutdown(self) -> None:
        """Stop dashboard server and close the database connection."""
        if self._dashboard:
            try:
                await self._dashboard.stop()
            except Exception as e:
                logger.warning(f"Dashboard shutdown error: {e}")
        if self._store:
            self._store.close()
            logger.debug("Observatory store closed")
