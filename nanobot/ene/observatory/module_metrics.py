"""Structured per-module event recording for the observatory.

Each module gets a ModuleMetrics instance scoped to its name.
Events go to SQLite (persistent, queryable) AND optionally to the
LiveTracer (real-time SSE dashboard). This bridges the gap between
operational metrics (cost, latency) and process metrics (WHY decisions
were made).

Design choices:
    - Module-scoped: each instance has a fixed module_name
    - Trace ID: set per message batch, links all module events across pipeline
    - Dual output: SQLite for queries, LiveTracer for real-time dashboard
    - span() context manager: paired start/complete with auto duration
    - Failure-safe: recording errors are logged, never raised

References:
    - AgentTrace (arxiv 2602.10133): start/complete event pairs
    - XAgen (arxiv 2512.17896): module failure attribution
    - MELT framework: Metrics, Events, Logs, Traces adapted for AI agents
"""

from __future__ import annotations

import json
import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.ene.observatory.store import MetricsStore
    from nanobot.agent.live_trace import LiveTracer


class ModuleMetrics:
    """Structured per-module event recording.

    Each module gets an instance scoped to its name.
    Events go to SQLite (queryable) AND optionally to LiveTracer (real-time SSE).

    Usage::

        metrics = ModuleMetrics("tracker", store, tracer)
        metrics.set_trace_id(trace_id)  # Called at pipeline entry

        # Fire-and-forget event
        metrics.record("thread_created", channel_key, thread_id="abc", method="reply_to")

        # Paired span with automatic duration
        with metrics.span("context_built", channel_key):
            ... # do work
    """

    def __init__(
        self,
        module_name: str,
        store: "MetricsStore | None" = None,
        tracer: "LiveTracer | None" = None,
    ) -> None:
        self._module = module_name
        self._store = store
        self._tracer = tracer
        self._trace_id: str | None = None

    @property
    def module_name(self) -> str:
        """The module this metrics instance is scoped to."""
        return self._module

    def set_trace_id(self, trace_id: str | None) -> None:
        """Set the trace ID for the current message batch.

        Called at debounce_flush — links all module events for one
        processing cycle across the full pipeline.
        """
        self._trace_id = trace_id

    def record(
        self,
        event_type: str,
        channel_key: str = "",
        duration_ms: int | None = None,
        **data: Any,
    ) -> None:
        """Record a module event to SQLite + optionally emit to LiveTracer.

        Args:
            event_type: Module-specific event (e.g. "thread_created", "scored").
            channel_key: Channel this event relates to.
            duration_ms: Optional duration for timed events.
            **data: Event-specific payload (must be JSON-serializable).
        """
        timestamp = datetime.now().isoformat()

        # Persist to SQLite
        if self._store:
            try:
                self._store.record_module_event(
                    timestamp=timestamp,
                    module=self._module,
                    event_type=event_type,
                    channel_key=channel_key,
                    data=data if data else None,
                    duration_ms=duration_ms,
                    trace_id=self._trace_id,
                )
            except Exception as e:
                logger.debug(f"ModuleMetrics.record failed ({self._module}/{event_type}): {e}")

        # Also emit to LiveTracer for real-time dashboard
        if self._tracer:
            try:
                live_data: dict[str, Any] = {"module": self._module, **data}
                if duration_ms is not None:
                    live_data["duration_ms"] = duration_ms
                if self._trace_id:
                    live_data["trace_id"] = self._trace_id
                self._tracer.emit(
                    f"mod_{self._module}_{event_type}",
                    channel_key,
                    **live_data,
                )
            except Exception as e:
                logger.debug(f"ModuleMetrics.emit failed ({self._module}/{event_type}): {e}")

    @contextmanager
    def span(
        self,
        event_type: str,
        channel_key: str = "",
        **data: Any,
    ) -> Iterator[dict[str, Any]]:
        """Paired start/complete event with automatic duration tracking.

        Yields a mutable dict — add keys to it inside the block and
        they'll be included in the recorded event.

        Usage::

            with metrics.span("context_built", channel_key) as span_data:
                result = build_context()
                span_data["active_threads"] = len(result.active)
                span_data["total_messages"] = result.total
        """
        extra: dict[str, Any] = dict(data)
        start = time.perf_counter()
        try:
            yield extra
        finally:
            elapsed_ms = int((time.perf_counter() - start) * 1000)
            self.record(event_type, channel_key, duration_ms=elapsed_ms, **extra)


class NullModuleMetrics(ModuleMetrics):
    """No-op metrics for when observatory is disabled or unavailable.

    All methods are silent no-ops. Used as a safe default so modules
    don't need to check ``if self._metrics:`` everywhere.
    """

    def __init__(self) -> None:
        super().__init__("null", store=None, tracer=None)

    def record(self, event_type: str, channel_key: str = "", duration_ms: int | None = None, **data: Any) -> None:
        pass

    @contextmanager
    def span(self, event_type: str, channel_key: str = "", **data: Any) -> Iterator[dict[str, Any]]:
        extra: dict[str, Any] = dict(data)
        yield extra
