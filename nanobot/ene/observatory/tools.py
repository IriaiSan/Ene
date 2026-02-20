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

class ViewModuleTool(Tool):
    """View module-level metrics and recent events."""

    def __init__(self, store: "MetricsStore"):
        self._store = store

    @property
    def name(self) -> str:
        return "view_module"

    @property
    def description(self) -> str:
        return (
            "View module-level metrics: thread stats, classification distribution, "
            "daemon success rate, memory budget, cleaning stats. "
            "Use module='tracker' for threads, 'signals' for classification, "
            "'daemon' for pre-classification, 'memory' for sleep agent, "
            "'cleaning' for response cleaning. Use trace_id to trace a single "
            "message batch across all modules. Omit module for an overview."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "module": {
                    "type": "string",
                    "description": "Module name: tracker, signals, daemon, memory, cleaning. Omit for overview.",
                    "enum": ["tracker", "signals", "daemon", "memory", "cleaning"],
                },
                "trace_id": {
                    "type": "string",
                    "description": "Trace ID to view all events for a single message batch across modules.",
                },
                "hours": {
                    "type": "integer",
                    "description": "Time window in hours (default 24).",
                    "default": 24,
                },
            },
        }

    async def execute(self, **kwargs: Any) -> str:
        module = kwargs.get("module")
        trace_id = kwargs.get("trace_id")
        hours = min(int(kwargs.get("hours", 24)), 168)

        try:
            if trace_id:
                return self._format_trace(trace_id)
            elif module:
                return self._format_module(module, hours)
            else:
                return self._format_overview(hours)
        except Exception as e:
            return f"Error loading module data: {e}"

    def _format_overview(self, hours: int) -> str:
        lines = [f"Module Health Overview (last {hours}h):", ""]

        # Thread stats
        ts = self._store.get_thread_stats(hours=hours)
        lines.append(f"tracker: {ts['threads_created']} threads created, "
                      f"{ts['total_assignments']} assignments, "
                      f"avg {ts['avg_messages_per_thread']:.1f} msg/thread")

        # Classification stats
        cs = self._store.get_classification_stats(hours=hours)
        if cs["total"] > 0:
            d = cs["distribution"]
            lines.append(f"signals: {cs['total']} classified — "
                          f"R:{d.get('RESPOND', 0)} C:{d.get('CONTEXT', 0)} D:{d.get('DROP', 0)} "
                          f"(avg confidence: {cs['avg_confidence']:.2f})")
        else:
            lines.append("signals: no classifications recorded")

        # Daemon
        dm = self._store.get_module_summary("daemon", hours=hours)
        bt = dm.get("by_type", {})
        ok = bt.get("classified", {}).get("count", 0)
        to = bt.get("timeout", {}).get("count", 0)
        lines.append(f"daemon: {ok} success, {to} timeouts")

        # Cleaning
        cl = self._store.get_module_summary("cleaning", hours=hours)
        cl_bt = cl.get("by_type", {})
        cleaned = cl_bt.get("cleaned", {}).get("count", 0)
        lines.append(f"cleaning: {cleaned} responses cleaned")

        # Memory
        mm = self._store.get_module_summary("memory", hours=hours)
        mm_bt = mm.get("by_type", {})
        facts = mm_bt.get("facts_extracted", {}).get("count", 0)
        refl = mm_bt.get("reflection_generated", {}).get("count", 0)
        lines.append(f"memory: {facts} extractions, {refl} reflections")

        # Prompt version
        try:
            from nanobot.agent.prompts.loader import PromptLoader
            lines.append(f"prompts: v{PromptLoader().version}")
        except Exception:
            pass

        return "\n".join(lines)

    def _format_module(self, module: str, hours: int) -> str:
        lines = [f"Module: {module} (last {hours}h)", ""]

        if module == "tracker":
            ts = self._store.get_thread_stats(hours=hours)
            lines.append(f"Threads created: {ts['threads_created']}")
            lines.append(f"Total assignments: {ts['total_assignments']}")
            if ts["assignment_methods"]:
                lines.append("Assignment methods:")
                for m, c in ts["assignment_methods"].items():
                    lines.append(f"  {m}: {c}")
            lines.append(f"Avg lifespan: {ts['avg_lifespan_ms'] / 1000:.1f}s")
            lines.append(f"Avg messages/thread: {ts['avg_messages_per_thread']:.1f}")
            lines.append(f"Avg active threads: {ts['avg_active_threads']:.1f}")
            lines.append(f"Avg background threads: {ts['avg_background_threads']:.1f}")

        elif module == "signals":
            cs = self._store.get_classification_stats(hours=hours)
            lines.append(f"Total: {cs['total']}")
            if cs["distribution"]:
                lines.append("Distribution:")
                for k, v in cs["distribution"].items():
                    pct = v / cs["total"] * 100 if cs["total"] else 0
                    lines.append(f"  {k}: {v} ({pct:.0f}%)")
            lines.append(f"Avg confidence: {cs['avg_confidence']:.3f}")
            if cs["feature_averages"]:
                lines.append("Feature averages:")
                for f, v in sorted(cs["feature_averages"].items(), key=lambda x: -x[1]):
                    lines.append(f"  {f}: {v:.2f}")
            lines.append(f"Overrides: {cs['override_count']}")

        elif module in ("daemon", "memory", "cleaning"):
            summary = self._store.get_module_summary(module, hours=hours)
            lines.append(f"Total events: {summary['total_events']}")
            if summary["by_type"]:
                lines.append("By type:")
                for t, info in summary["by_type"].items():
                    avg_dur = f" (avg {info['avg_duration_ms']:.0f}ms)" if info.get("avg_duration_ms") else ""
                    lines.append(f"  {t}: {info['count']}{avg_dur}")

        # Recent events
        events = self._store.get_module_events(module, hours=hours, limit=5)
        if events:
            lines.append("")
            lines.append("Recent events:")
            for e in events:
                ts_short = e["timestamp"][11:19] if len(e["timestamp"]) > 19 else e["timestamp"]
                data_str = ""
                if e.get("data"):
                    # Show first few key-value pairs
                    pairs = list(e["data"].items())[:3]
                    data_str = " " + " ".join(f"{k}={v}" for k, v in pairs)
                lines.append(f"  [{ts_short}] {e['event_type']}{data_str}")

        return "\n".join(lines)

    def _format_trace(self, trace_id: str) -> str:
        events = self._store.get_trace_events(trace_id)
        if not events:
            return f"No events found for trace_id: {trace_id}"

        lines = [f"Trace: {trace_id} ({len(events)} events)", ""]
        for e in events:
            ts_short = e["timestamp"][11:19] if len(e["timestamp"]) > 19 else e["timestamp"]
            dur = f" ({e['duration_ms']}ms)" if e.get("duration_ms") else ""
            data_str = ""
            if e.get("data"):
                pairs = list(e["data"].items())[:4]
                data_str = " | " + ", ".join(f"{k}={v}" for k, v in pairs)
            lines.append(f"  [{ts_short}] {e['module']}/{e['event_type']}{dur}{data_str}")

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
