"""Report generator — creates daily and weekly summaries for Ene to DM Dad.

Formats metrics data into readable reports that get sent as Discord/Telegram
messages. Designed to be concise and useful, not overwhelming.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.ene.observatory.store import MetricsStore


class ReportGenerator:
    """Generates human-readable reports from observatory data."""

    def __init__(self, store: "MetricsStore"):
        self._store = store

    def daily_report(self, date: str | None = None) -> str:
        """Generate a daily summary report.

        Args:
            date: YYYY-MM-DD string. Defaults to yesterday.

        Returns:
            Formatted report string ready for messaging.
        """
        if date is None:
            date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

        summary = self._store.get_day_summary(date)

        if summary["total_calls"] == 0:
            return f"Daily Report \u2014 {date}\nNo activity recorded."

        lines = [
            f"Daily Report \u2014 {date}",
            f"Cost: ${summary['total_cost_usd']:.4f} | "
            f"Tokens: {summary['total_tokens']:,} | "
            f"Calls: {summary['total_calls']}",
        ]

        # Cost by type
        type_breakdown = summary.get("call_type_breakdown", {})
        if type_breakdown:
            type_parts = []
            for ct, data in sorted(type_breakdown.items(), key=lambda x: -x[1]["cost"]):
                type_parts.append(f"{ct} ${data['cost']:.4f}")
            lines.append(f"Breakdown: {' | '.join(type_parts)}")

        # Top callers
        caller_breakdown = summary.get("caller_breakdown", {})
        if caller_breakdown:
            caller_parts = []
            for caller, data in sorted(caller_breakdown.items(), key=lambda x: -x[1]["calls"]):
                if caller == "system":
                    continue
                name = caller.split(":")[-1][:12]  # Shorten caller IDs
                caller_parts.append(f"{name} ({data['calls']} msgs)")
            if caller_parts:
                lines.append(f"Top users: {', '.join(caller_parts[:5])}")

        # Error + latency
        lines.append(
            f"Errors: {summary['error_count']} | "
            f"Avg latency: {summary['avg_latency_ms']:.0f}ms"
        )

        return "\n".join(lines)

    def weekly_report(self) -> str:
        """Generate a weekly summary report (last 7 days)."""
        daily_data = self._store.get_cost_by_day(7)

        if not daily_data:
            return "Weekly Report\nNo activity in the last 7 days."

        total_cost = sum(d["cost"] for d in daily_data)
        total_calls = sum(d["calls"] for d in daily_data)
        total_tokens = sum(d["tokens"] for d in daily_data)
        avg_daily = total_cost / max(len(daily_data), 1)

        lines = [
            f"Weekly Report \u2014 Last 7 Days",
            f"Total cost: ${total_cost:.4f} | "
            f"Calls: {total_calls:,} | "
            f"Tokens: {total_tokens:,}",
            f"Avg daily: ${avg_daily:.4f}/day",
        ]

        # Model breakdown
        model_data = self._store.get_cost_by_model(7)
        if model_data:
            lines.append("By model:")
            for m in model_data[:5]:
                model_short = m["model"].split("/")[-1][:20]
                lines.append(
                    f"  {model_short}: ${m['cost']:.4f} "
                    f"({m['calls']} calls, {m['avg_latency']:.0f}ms avg)"
                )

        # Daily trend
        if len(daily_data) >= 2:
            lines.append("Daily trend:")
            for d in daily_data:
                bar = "\u2588" * max(1, int(d["cost"] / max(avg_daily, 0.0001) * 5))
                lines.append(f"  {d['date']}: ${d['cost']:.4f} {bar}")

        # Error rate
        error_data = self._store.get_error_rate(168)  # 7 days
        if error_data["total_calls"] > 0:
            lines.append(
                f"Error rate: {error_data['error_rate']:.1%} "
                f"({error_data['error_count']}/{error_data['total_calls']})"
            )

        return "\n".join(lines)

    def cost_report(self, days: int = 30) -> str:
        """Generate a detailed cost breakdown report."""
        daily_data = self._store.get_cost_by_day(days)
        model_data = self._store.get_cost_by_model(days)
        type_data = self._store.get_cost_by_type(days)
        caller_data = self._store.get_cost_by_caller(days)

        total_cost = sum(d["cost"] for d in daily_data)

        lines = [f"Cost Report \u2014 Last {days} Days", f"Total: ${total_cost:.4f}"]

        if model_data:
            lines.append("\nBy model:")
            for m in model_data:
                pct = (m["cost"] / total_cost * 100) if total_cost > 0 else 0
                lines.append(f"  {m['model']}: ${m['cost']:.4f} ({pct:.0f}%)")

        if type_data:
            lines.append("\nBy type:")
            for t in type_data:
                pct = (t["cost"] / total_cost * 100) if total_cost > 0 else 0
                lines.append(f"  {t['call_type']}: ${t['cost']:.4f} ({pct:.0f}%)")

        if caller_data:
            lines.append("\nBy caller:")
            for c in caller_data[:10]:
                pct = (c["cost"] / total_cost * 100) if total_cost > 0 else 0
                lines.append(f"  {c['caller_id']}: ${c['cost']:.4f} ({pct:.0f}%)")

        return "\n".join(lines)

    def quick_status(self) -> str:
        """One-line status string (for embedding in context or quick checks)."""
        summary = self._store.get_today_summary()
        if summary["total_calls"] == 0:
            return "Observatory: no calls today"

        return (
            f"Today: {summary['total_calls']} calls, "
            f"${summary['total_cost_usd']:.4f}, "
            f"{summary['avg_latency_ms']:.0f}ms avg"
        )
