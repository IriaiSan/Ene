"""Audit system — captures all events from a lab run for post-analysis.

Wraps the existing LiveTracer (17+ event types) to persist ALL events
for post-run analysis. Attaches to the tracer's emit() method to collect
events transparently.

Usage:
    audit = AuditCollector()
    audit.attach_to_tracer(lab.agent_loop._live)
    # ... run messages ...
    audit.save(lab.paths.audit_dir / "run.jsonl")
    print(audit.summary())
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger


class AuditCollector:
    """Collects all tracer events for post-run audit.

    Attaches to a LiveTracer by monkey-patching emit() to also
    record events here. All original behavior is preserved.
    """

    def __init__(self) -> None:
        self._events: list[dict[str, Any]] = []
        self._prompts: list[dict[str, Any]] = []
        self._original_emit: Any = None
        self._original_emit_prompt: Any = None
        self._tracer: Any = None

    def attach_to_tracer(self, tracer: Any) -> None:
        """Wrap tracer.emit() and emit_prompt() to also collect events here.

        Args:
            tracer: A LiveTracer instance.
        """
        self._tracer = tracer
        self._original_emit = tracer.emit
        self._original_emit_prompt = getattr(tracer, "emit_prompt", None)

        def wrapped_emit(event_type: str, channel_key: str = "", **data: Any) -> None:
            # Record for audit
            event = {
                "type": event_type,
                "ts": datetime.now().isoformat(),
                "channel_key": channel_key,
                **data,
            }
            self._events.append(event)
            # Call original
            self._original_emit(event_type, channel_key, **data)

        tracer.emit = wrapped_emit

        if self._original_emit_prompt:
            def wrapped_emit_prompt(prompt_type: str, channel_key: str = "", **data: Any) -> None:
                prompt_event = {
                    "type": prompt_type,
                    "ts": datetime.now().isoformat(),
                    "channel_key": channel_key,
                    **data,
                }
                self._prompts.append(prompt_event)
                self._original_emit_prompt(prompt_type, channel_key, **data)

            tracer.emit_prompt = wrapped_emit_prompt

    def detach(self) -> None:
        """Restore original tracer methods."""
        if self._tracer and self._original_emit:
            self._tracer.emit = self._original_emit
        if self._tracer and self._original_emit_prompt:
            self._tracer.emit_prompt = self._original_emit_prompt
        self._tracer = None

    # ── Persistence ───────────────────────────────────────

    def save(self, path: Path) -> Path:
        """Save all events to a JSONL file.

        Args:
            path: Output file path.

        Returns:
            The path written to.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for event in self._events:
                f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            # Separate section for prompts
            for prompt in self._prompts:
                f.write(json.dumps(prompt, ensure_ascii=False, default=str) + "\n")

        logger.debug(f"Audit saved: {len(self._events)} events, {len(self._prompts)} prompts → {path}")
        return path

    @staticmethod
    def load(path: Path) -> list[dict[str, Any]]:
        """Load events from a JSONL audit file."""
        events = []
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    events.append(json.loads(line))
        return events

    # ── Querying ──────────────────────────────────────────

    def get_events(self, event_type: str | None = None) -> list[dict[str, Any]]:
        """Get events, optionally filtered by type."""
        if event_type is None:
            return list(self._events)
        return [e for e in self._events if e.get("type") == event_type]

    def get_classifications(self) -> list[dict[str, Any]]:
        """Get all classification events."""
        return self.get_events("classification")

    def get_prompts(self) -> list[dict[str, Any]]:
        """Get all prompt log entries."""
        return list(self._prompts)

    def get_responses(self) -> list[dict[str, Any]]:
        """Get all response_sent events."""
        return self.get_events("response_sent")

    def get_errors(self) -> list[dict[str, Any]]:
        """Get all error events."""
        return self.get_events("error")

    # ── Summary ───────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        """Generate a summary of the audit trail."""
        type_counts: dict[str, int] = {}
        for event in self._events:
            t = event.get("type", "unknown")
            type_counts[t] = type_counts.get(t, 0) + 1

        classifications: dict[str, int] = {}
        for event in self.get_classifications():
            cls = event.get("classification", "unknown")
            classifications[cls] = classifications.get(cls, 0) + 1

        return {
            "total_events": len(self._events),
            "total_prompts": len(self._prompts),
            "event_types": type_counts,
            "classifications": classifications,
            "errors": len(self.get_errors()),
            "responses": len(self.get_responses()),
        }

    def clear(self) -> None:
        """Clear all collected events."""
        self._events.clear()
        self._prompts.clear()
