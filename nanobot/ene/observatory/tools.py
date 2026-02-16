"""Observatory tools — let Ene check her own metrics.

These tools are Dad-only (restricted via the same DAD_IDS mechanism
in loop.py). They give Ene awareness of her resource usage.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.ene.observatory.store import MetricsStore
    from nanobot.ene.observatory.reporter import ReportGenerator
    from nanobot.ene.observatory.experiments import ExperimentEngine


class ViewMetricsTool(Tool):
    """View observatory metrics — cost, calls, latency, errors."""

    def __init__(self, store: "MetricsStore", reporter: "ReportGenerator"):
        self._store = store
        self._reporter = reporter

    @property
    def name(self) -> str:
        return "view_metrics"

    @property
    def description(self) -> str:
        return (
            "View your own metrics — cost, token usage, call counts, latency, errors. "
            "Use period='today' for today's summary, 'week' for weekly report, "
            "'cost' for detailed cost breakdown."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "period": {
                    "type": "string",
                    "description": "Time period: 'today', 'week', or 'cost'",
                    "enum": ["today", "week", "cost"],
                    "default": "today",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        period = kwargs.get("period", "today")

        try:
            if period == "today":
                return self._reporter.quick_status() + "\n\n" + self._reporter.daily_report(None)
            elif period == "week":
                return self._reporter.weekly_report()
            elif period == "cost":
                return self._reporter.cost_report(30)
            else:
                return self._reporter.quick_status()
        except Exception as e:
            return f"Error loading metrics: {e}"


class ViewExperimentsTool(Tool):
    """View A/B experiment status and results."""

    def __init__(self, store: "MetricsStore"):
        self._store = store

    @property
    def name(self) -> str:
        return "view_experiments"

    @property
    def description(self) -> str:
        return (
            "View A/B experiment status and results. "
            "Shows active experiments, variant stats, and winner suggestions."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "experiment_id": {
                    "type": "string",
                    "description": "Specific experiment ID to view details for. Omit for list of all experiments.",
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        exp_id = kwargs.get("experiment_id")

        try:
            if exp_id:
                return self._format_experiment_detail(exp_id)
            else:
                return self._format_experiment_list()
        except Exception as e:
            return f"Error loading experiments: {e}"

    def _format_experiment_list(self) -> str:
        experiments = self._store.get_all_experiments()
        if not experiments:
            return "No experiments found."

        lines = [f"Experiments ({len(experiments)} total):"]
        for exp in experiments:
            status = exp["status"]
            name = exp["name"]
            variants = len(exp.get("variants", []))
            lines.append(f"  [{status}] {exp['id']}: {name} ({variants} variants)")

        return "\n".join(lines)

    def _format_experiment_detail(self, exp_id: str) -> str:
        exp = self._store.get_experiment(exp_id)
        if not exp:
            return f"Experiment '{exp_id}' not found."

        results = self._store.get_experiment_results(exp_id)
        variants = results.get("variants", {})

        lines = [
            f"Experiment: {exp['name']} ({exp['status']})",
            f"ID: {exp['id']}",
            f"Target: {exp.get('target_calls', 'N/A')} calls",
            f"Total calls: {results['total_calls']}",
            "",
            "Variants:",
        ]

        for vid, stats in variants.items():
            lines.append(f"  {vid}:")
            lines.append(f"    Calls: {stats.get('calls', 0)}")
            lines.append(f"    Avg cost: ${stats.get('avg_cost', 0):.4f}")
            lines.append(f"    Avg latency: {stats.get('avg_latency', 0):.0f}ms")
            if "avg_quality" in stats:
                lines.append(f"    Avg quality: {stats['avg_quality']:.2f}/5")

        return "\n".join(lines)
