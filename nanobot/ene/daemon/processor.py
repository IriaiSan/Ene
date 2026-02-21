"""DaemonProcessor — the LLM-powered subconscious pre-processor.

Runs a free LLM call on every RESPOND-classified message to:
1. Re-classify with LLM intelligence (upgrades hardcoded _classify_message)
2. Detect jailbreak/injection/impersonation attempts
3. Detect implicit Ene references
4. Analyze tone and topic
5. Return structured analysis for context injection

Falls back to hardcoded classification on failure or timeout.
"""

from __future__ import annotations

import asyncio
import json
import re
import time as _time
from typing import TYPE_CHECKING

from loguru import logger

from nanobot.ene.daemon.models import (
    Classification,
    DaemonResult,
    SecurityFlag,
    DEFAULT_FREE_MODELS,
)

if TYPE_CHECKING:
    from nanobot.ene.observatory.collector import MetricsCollector
    from nanobot.ene.observatory.module_metrics import ModuleMetrics
    from nanobot.providers.base import LLMProvider

# Module-level metrics instance — set by set_metrics() during init.
_metrics: "ModuleMetrics | None" = None


def set_metrics(metrics: "ModuleMetrics") -> None:
    """Attach a ModuleMetrics instance for daemon observability."""
    global _metrics
    _metrics = metrics

# Word-boundary match to avoid false positives ("generic", "scene", etc.)
_ENE_PATTERN = re.compile(r"\bene\b", re.IGNORECASE)


# ── Daemon system prompt — loaded from prompts/daemon_system.txt ──
# Prompt version tracked via manifest.json for observability correlation.

from nanobot.agent.prompts.loader import PromptLoader as _PromptLoader

_prompt_loader = _PromptLoader()

# Module-level constant — loaded from file, no template vars needed
DAEMON_PROMPT = _prompt_loader.load("daemon_system")


class DaemonProcessor:
    """Runs a free LLM call to classify and sanitize messages."""

    def __init__(
        self,
        provider: LLMProvider,
        model: str | None = None,
        fallback_models: list[str] | None = None,
        temperature: float = 0.1,
        timeout_seconds: float = 5.0,
        observatory: MetricsCollector | None = None,
    ):
        self._provider = provider
        self._model = model
        self._fallback_models = fallback_models or list(DEFAULT_FREE_MODELS)
        self._temperature = temperature
        self._timeout = timeout_seconds
        self._observatory = observatory

        # Model rotation tracking
        self._model_index = 0
        self._model_failures: dict[str, int] = {}

    # ── Public API ─────────────────────────────────────────────────────

    async def process(
        self,
        content: str,
        sender_name: str,
        sender_id: str,
        is_dad: bool,
        metadata: dict | None = None,
        channel_state=None,
        recent_context: list[str] | None = None,
    ) -> DaemonResult:
        """Process a message through the daemon.

        Returns DaemonResult with classification, security analysis,
        and optionally sanitized content. Falls back to math classifier
        (if channel_state available) or regex on failure/timeout.
        """
        start = _time.perf_counter()

        try:
            result = await asyncio.wait_for(
                self._llm_process(content, sender_name, sender_id, is_dad, metadata, recent_context),
                timeout=self._timeout,
            )
            result.latency_ms = int((_time.perf_counter() - start) * 1000)

            # Record successful classification
            if _metrics:
                _metrics.record(
                    "classified",
                    model_used=result.model_used,
                    classification=result.classification.value,
                    confidence=result.confidence,
                    topic=result.topic_summary,
                    emotional_tone=result.emotional_tone,
                    security_flags=[f.type for f in result.security_flags],
                    latency_ms=result.latency_ms,
                )

            return result

        except asyncio.TimeoutError:
            model = self._get_current_model()
            logger.warning(f"Daemon: timeout after {self._timeout}s on {model}, falling back")
            self._record_failure(model)
            self._rotate_model()

            if _metrics:
                _metrics.record(
                    "timeout",
                    model_attempted=model,
                    fallback_to="math_classifier" if channel_state else "regex",
                )

            return self._hardcoded_fallback(content, sender_id, is_dad, start, metadata, channel_state)

        except Exception as e:
            model = self._get_current_model()
            logger.warning(f"Daemon: LLM failed on {model} ({e}), falling back")
            self._record_failure(model)

            old_model = model
            self._rotate_model()
            new_model = self._get_current_model()

            if _metrics:
                _metrics.record(
                    "model_rotation",
                    from_model=old_model,
                    to_model=new_model,
                    reason=str(e)[:100],
                )

            return self._hardcoded_fallback(content, sender_id, is_dad, start, metadata, channel_state)

    # ── Internal ───────────────────────────────────────────────────────

    def _get_current_model(self) -> str:
        """Get the current model, rotating through fallbacks.

        NEVER falls back to openrouter/auto — that routes to expensive paid
        models. If no fallback list, use the first DEFAULT_FREE_MODELS entry.
        """
        if self._model:
            return self._model
        if not self._fallback_models:
            return DEFAULT_FREE_MODELS[0] if DEFAULT_FREE_MODELS else "deepseek/deepseek-r1-0528:free"
        return self._fallback_models[self._model_index % len(self._fallback_models)]

    def _rotate_model(self) -> None:
        """Rotate to next model after failure."""
        if self._fallback_models and not self._model:
            self._model_index = (self._model_index + 1) % len(self._fallback_models)
            logger.debug(f"Daemon: rotated to {self._get_current_model()}")

    def _record_failure(self, model: str) -> None:
        """Track consecutive failures per model."""
        self._model_failures[model] = self._model_failures.get(model, 0) + 1

    async def _llm_process(
        self,
        content: str,
        sender_name: str,
        sender_id: str,
        is_dad: bool,
        metadata: dict | None,
        recent_context: list[str] | None = None,
    ) -> DaemonResult:
        """Make the actual LLM call for daemon analysis."""
        model = self._get_current_model()

        # Build compact user message with conversation context
        user_msg = f"Sender: {sender_name} (ID: {sender_id})"
        if is_dad:
            user_msg += " [THIS IS DAD - respond unless clearly talking to someone else]"
        if metadata and metadata.get("is_reply_to_ene"):
            user_msg += " [REPLYING TO ENE]"
        if metadata and metadata.get("_is_stale"):
            stale_min = metadata.get("_stale_minutes", "?")
            user_msg += f" [MESSAGE IS STALE - sent {stale_min} min ago]"
        if recent_context:
            user_msg += "\n\nRecent chat:\n" + "\n".join(recent_context)
        user_msg += f"\n\nNew message to classify:\n{content[:2000]}"

        messages = [
            {"role": "system", "content": DAEMON_PROMPT},
            {"role": "user", "content": user_msg},
        ]

        obs_start = _time.perf_counter()
        response = await self._provider.chat(
            messages=messages,
            model=model,
            max_tokens=512,
            temperature=self._temperature,
        )

        # Track in observatory
        if self._observatory:
            self._observatory.record(
                response, call_type="daemon", model=model,
                caller_id=sender_id, latency_start=obs_start,
            )

        # Parse response
        result = self._parse_response(response.content or "", model)

        # Reset failure count on success
        self._model_failures[model] = 0

        return result

    def _parse_response(self, text: str, model: str) -> DaemonResult:
        """Parse daemon LLM response into DaemonResult.

        Robust JSON parsing: try raw → markdown block → brace extract.
        Same pattern used by sleep_agent and watchdog.
        """
        result = DaemonResult(model_used=model)

        if not text:
            result.fallback_used = True
            return result

        # Try parsing JSON from response
        data = self._extract_json(text)

        if not data or not isinstance(data, dict):
            logger.debug(f"Daemon: failed to parse JSON from {model}: {text[:200]}")
            result.fallback_used = True
            return result

        # Map fields
        classification_str = str(data.get("classification", "context")).lower()
        if classification_str in ("respond", "context", "drop"):
            result.classification = Classification(classification_str)

        result.confidence = min(1.0, max(0.0, float(data.get("confidence", 0.8))))
        result.classification_reason = str(data.get("reason", ""))
        result.implicit_ene_reference = bool(data.get("implicit_ene_ref", False))
        result.topic_summary = str(data.get("topic", ""))
        result.emotional_tone = str(data.get("tone", "neutral"))

        # Security flags
        for flag_data in data.get("security_flags", []):
            if isinstance(flag_data, dict):
                flag_type = str(flag_data.get("type", "unknown"))
                flag_severity = str(flag_data.get("severity", "low"))
                if flag_severity not in ("low", "medium", "high"):
                    flag_severity = "low"
                result.security_flags.append(SecurityFlag(
                    type=flag_type,
                    severity=flag_severity,
                    description=str(flag_data.get("description", "")),
                ))

        return result

    @staticmethod
    def _extract_json(text: str) -> dict | None:
        """Extract JSON from LLM response, handling markdown blocks etc."""
        # Try raw JSON first
        try:
            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            pass

        # Try extracting from markdown code blocks
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except (json.JSONDecodeError, ValueError):
                pass

        # Try extracting first {...} block
        brace_match = re.search(r"\{[\s\S]*\}", text)
        if brace_match:
            try:
                return json.loads(brace_match.group())
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    def _hardcoded_fallback(
        self, content: str, sender_id: str, is_dad: bool, start: float,
        metadata: dict | None = None,
        channel_state=None,
    ) -> DaemonResult:
        """Fallback classification when daemon LLM fails or times out.

        Two modes:
        1. Math classifier (when channel_state is available): Naive Bayes
           log-odds over 8 features. Under 1ms, no API calls.
        2. Regex fallback (no state): simple \bene\b pattern match.
        """
        result = DaemonResult(
            fallback_used=True,
            latency_ms=int((_time.perf_counter() - start) * 1000),
            model_used="hardcoded_fallback",
        )

        is_stale = bool(metadata and metadata.get("_is_stale"))

        # ── Math classifier path (preferred) ───────────────────────
        if channel_state is not None:
            from nanobot.ene.conversation.signals import classify_with_state

            cls, score, features = classify_with_state(
                content,
                sender_id,
                channel_state,
                is_at_mention=bool(metadata and metadata.get("is_at_mention")),
                is_reply_to_ene=bool(metadata and metadata.get("is_reply_to_ene")),
                is_in_ene_thread=bool(metadata and metadata.get("is_in_ene_thread")),
            )

            # Dad override: never DROP Dad
            if is_dad and cls == "drop":
                cls = "context"

            # Stale non-Dad override: cap at CONTEXT unless strong signal
            if is_stale and not is_dad and cls == "respond" and score < 0.85:
                cls = "context"

            result.classification = Classification[cls.upper()]
            result.confidence = score
            # Show top contributing feature in reason
            top = max(features, key=features.get) if features else "none"
            result.classification_reason = f"math({score:.2f}): {top}={features.get(top, 0):.1f}"
            result.model_used = "math_classifier"
            return result

        # ── Regex fallback (no channel state) ──────────────────────
        has_ene_signal = bool(_ENE_PATTERN.search(content)) or bool(
            metadata and metadata.get("is_reply_to_ene")
        )

        if is_dad:
            if has_ene_signal:
                result.classification = Classification.RESPOND
                result.classification_reason = "Dad message with Ene relevance"
            else:
                result.classification = Classification.CONTEXT
                result.classification_reason = "Dad talking to someone else"
            return result

        # Stale non-Dad messages → CONTEXT (don't respond to old messages)
        if is_stale and not has_ene_signal:
            result.classification = Classification.CONTEXT
            stale_min = (metadata or {}).get("_stale_minutes", "?")
            result.classification_reason = f"Stale message ({stale_min}min old), no Ene mention"
            return result

        if has_ene_signal:
            result.classification = Classification.RESPOND
            result.classification_reason = "Mentions Ene by name"
        else:
            result.classification = Classification.CONTEXT
            result.classification_reason = "No Ene mention, background chatter"

        return result
