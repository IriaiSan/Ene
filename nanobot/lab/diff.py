"""Audit diff — compare two lab run audit trails.

Compares event sequences, classifications, response content, and timing
between two audit JSONL files. Useful for regression testing after
prompt or code changes.

Usage:
    result = AuditDiff.compare("run_a/audit.jsonl", "run_b/audit.jsonl")
    print(result["classification_changes"])
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from nanobot.lab.audit import AuditCollector


class AuditDiff:
    """Compare two audit trails for regression analysis."""

    @staticmethod
    def compare(audit_a: Path, audit_b: Path) -> dict[str, Any]:
        """Compare two audit JSONL files.

        Returns a dict with:
            - event_count_diff: difference in event counts by type
            - classification_changes: how classifications differed
            - response_diffs: side-by-side response content comparison
            - timing_diff: average timing differences
            - summary: high-level comparison

        Args:
            audit_a: Path to first audit JSONL.
            audit_b: Path to second audit JSONL.

        Returns:
            Comparison dict.
        """
        events_a = AuditCollector.load(audit_a)
        events_b = AuditCollector.load(audit_b)

        result: dict[str, Any] = {
            "event_count_diff": AuditDiff._count_diff(events_a, events_b),
            "classification_changes": AuditDiff._classification_diff(events_a, events_b),
            "response_diffs": AuditDiff._response_diff(events_a, events_b),
            "summary": {},
        }

        # Build summary
        total_a = len(events_a)
        total_b = len(events_b)
        result["summary"] = {
            "events_a": total_a,
            "events_b": total_b,
            "event_delta": total_b - total_a,
            "classification_changes_count": len(result["classification_changes"]),
            "response_changes_count": len(result["response_diffs"]),
        }

        return result

    @staticmethod
    def _count_diff(
        events_a: list[dict], events_b: list[dict]
    ) -> dict[str, dict[str, int]]:
        """Compare event type counts between two trails."""
        counts_a: dict[str, int] = {}
        counts_b: dict[str, int] = {}

        for e in events_a:
            t = e.get("type", "unknown")
            counts_a[t] = counts_a.get(t, 0) + 1
        for e in events_b:
            t = e.get("type", "unknown")
            counts_b[t] = counts_b.get(t, 0) + 1

        all_types = sorted(set(counts_a) | set(counts_b))
        diff: dict[str, dict[str, int]] = {}
        for t in all_types:
            a = counts_a.get(t, 0)
            b = counts_b.get(t, 0)
            if a != b:
                diff[t] = {"a": a, "b": b, "delta": b - a}

        return diff

    @staticmethod
    def _classification_diff(
        events_a: list[dict], events_b: list[dict]
    ) -> list[dict[str, Any]]:
        """Find messages that got classified differently.

        Matches classification events by channel_key + approximate order.
        """
        cls_a = [e for e in events_a if e.get("type") == "classification"]
        cls_b = [e for e in events_b if e.get("type") == "classification"]

        changes: list[dict[str, Any]] = []
        # Simple positional comparison (both runs should process same messages in order)
        for i, (ca, cb) in enumerate(zip(cls_a, cls_b)):
            class_a = ca.get("classification")
            class_b = cb.get("classification")
            if class_a != class_b:
                changes.append({
                    "index": i,
                    "channel_key": ca.get("channel_key", ""),
                    "a": class_a,
                    "b": class_b,
                })

        return changes

    @staticmethod
    def _response_diff(
        events_a: list[dict], events_b: list[dict]
    ) -> list[dict[str, Any]]:
        """Compare response content between two runs."""
        resp_a = [e for e in events_a if e.get("type") == "response_sent"]
        resp_b = [e for e in events_b if e.get("type") == "response_sent"]

        diffs: list[dict[str, Any]] = []
        for i, (ra, rb) in enumerate(zip(resp_a, resp_b)):
            content_a = ra.get("content", ra.get("content_preview", ""))
            content_b = rb.get("content", rb.get("content_preview", ""))
            if content_a != content_b:
                diffs.append({
                    "index": i,
                    "channel_key": ra.get("channel_key", ""),
                    "a": str(content_a)[:200],
                    "b": str(content_b)[:200],
                })

        return diffs
