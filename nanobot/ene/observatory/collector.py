"""Metrics collector — captures every LLM call into the observatory.

Usage at call sites:

    start = time.perf_counter()
    response = await provider.chat(...)
    collector.record(response, call_type="response", model=model,
                     caller_id=caller_id, latency_start=start)
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.ene.observatory.pricing import calculate_cost
from nanobot.ene.observatory.store import LLMCallRecord, MetricsStore

if TYPE_CHECKING:
    from nanobot.providers.base import LLMResponse


class MetricsCollector:
    """Captures LLM call metrics and writes them to the store.

    Lightweight, fast, non-blocking. If recording fails, it logs
    and moves on — never disrupts the main conversation flow.
    """

    def __init__(self, store: MetricsStore):
        self._store = store
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def record(
        self,
        response: "LLMResponse",
        *,
        call_type: str,
        model: str,
        caller_id: str = "system",
        session_key: str = "",
        latency_start: float | None = None,
        experiment_id: str | None = None,
        variant_id: str | None = None,
    ) -> int | None:
        """Record an LLM call from its response.

        Args:
            response: The LLMResponse from the provider.
            call_type: Type of call (response, summary, diary, sleep, experiment).
            model: Model identifier used for this call.
            caller_id: Who triggered this (e.g., "discord:123456").
            session_key: Session identifier.
            latency_start: time.perf_counter() value from before the call.
            experiment_id: If this call is part of an experiment.
            variant_id: Which variant was used.

        Returns:
            Row ID of the inserted record, or None if recording failed.
        """
        if not self._enabled:
            return None

        try:
            # Extract usage
            usage = response.usage or {}
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

            # Calculate cost
            cost = calculate_cost(model, prompt_tokens, completion_tokens)

            # Calculate latency
            latency_ms = 0
            if latency_start is not None:
                latency_ms = int((time.perf_counter() - latency_start) * 1000)

            # Extract tool call names
            tool_names = [tc.name for tc in response.tool_calls] if response.tool_calls else []

            # Check for error
            error = None
            if response.finish_reason == "error":
                error = (response.content or "Unknown error")[:500]

            record = LLMCallRecord(
                timestamp=datetime.now().isoformat(),
                call_type=call_type,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
                caller_id=caller_id,
                session_key=session_key,
                tool_calls=tool_names,
                finish_reason=response.finish_reason or "stop",
                error=error,
                experiment_id=experiment_id,
                variant_id=variant_id,
            )

            row_id = self._store.record_call(record)
            logger.trace(
                f"Observatory: {call_type} | {model} | "
                f"{total_tokens}tok | ${cost:.4f} | {latency_ms}ms"
            )
            return row_id

        except Exception as e:
            # Never let metrics recording crash the main flow
            logger.warning(f"Observatory recording failed: {e}")
            return None

    def record_raw(
        self,
        *,
        call_type: str,
        model: str,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: int = 0,
        caller_id: str = "system",
        session_key: str = "",
        finish_reason: str = "stop",
        error: str | None = None,
        experiment_id: str | None = None,
        variant_id: str | None = None,
    ) -> int | None:
        """Record a call from raw values (for cases without an LLMResponse).

        Useful for sleep_agent or other places where you have the
        raw numbers but not the full response object.
        """
        if not self._enabled:
            return None

        try:
            total_tokens = prompt_tokens + completion_tokens
            cost = calculate_cost(model, prompt_tokens, completion_tokens)

            record = LLMCallRecord(
                timestamp=datetime.now().isoformat(),
                call_type=call_type,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cost_usd=cost,
                latency_ms=latency_ms,
                caller_id=caller_id,
                session_key=session_key,
                finish_reason=finish_reason,
                error=error,
                experiment_id=experiment_id,
                variant_id=variant_id,
            )

            return self._store.record_call(record)

        except Exception as e:
            logger.warning(f"Observatory raw recording failed: {e}")
            return None
