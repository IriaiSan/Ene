"""LiteLLM provider implementation for multi-provider support.

Includes model fallback with latency-based rotation: on timeout or error,
automatically retries once with the next model in the fallback list.
Adapted from daemon/processor.py rotation pattern.
"""

import asyncio
import json
import json_repair
import logging
import os
import time as _time
from typing import Any

import litellm
from litellm import acompletion

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest
from nanobot.providers.registry import find_by_model, find_gateway

logger = logging.getLogger(__name__)

# Default fallback models — all routed through OpenRouter (no new API keys)
# Order matters: primary first, cheapest/fastest last
DEFAULT_FALLBACK_MODELS: list[str] = [
    "deepseek/deepseek-v3.2",           # Primary: matches config.json
    "qwen/qwen3-235b-a22b",             # Fallback 1: strong reasoning, tool use
    "google/gemini-2.5-flash",           # Fallback 2: fast, decent quality
]

# Timeout for a single LLM call — generous but prevents infinite hangs
LLM_CALL_TIMEOUT: float = 45.0

# Recovery: how long to wait before probing primary model again after failure
RECOVERY_COOLDOWN: float = 300.0  # 5 minutes


class LiteLLMProvider(LLMProvider):
    """
    LLM provider using LiteLLM for multi-provider support.

    Supports OpenRouter, Anthropic, OpenAI, Gemini, MiniMax, and many other providers through
    a unified interface.  Provider-specific logic is driven by the registry
    (see providers/registry.py) — no if-elif chains needed here.

    Model fallback: on timeout or error, rotates to the next model in the
    fallback list and retries once. If the retry also fails, returns an error
    response. This prevents indefinite blocking when a model/provider is down.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_base: str | None = None,
        default_model: str = "anthropic/claude-opus-4-5",
        extra_headers: dict[str, str] | None = None,
        provider_name: str | None = None,
        fallback_models: list[str] | None = None,
        timeout: float = LLM_CALL_TIMEOUT,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.extra_headers = extra_headers or {}
        self._timeout = timeout

        # Model fallback rotation (adapted from daemon/processor.py)
        self._fallback_models = fallback_models or []
        self._model_index = 0
        self._model_failures: dict[str, int] = {}
        self._last_rotation_time: float = 0.0
        self._last_recovery_attempt: float = 0.0

        # Detect gateway / local deployment.
        # provider_name (from config key) is the primary signal;
        # api_key / api_base are fallback for auto-detection.
        self._gateway = find_gateway(provider_name, api_key, api_base)

        # Configure environment variables
        if api_key:
            self._setup_env(api_key, api_base, default_model)

        if api_base:
            litellm.api_base = api_base

        # Disable LiteLLM logging noise
        litellm.suppress_debug_info = True
        # Drop unsupported parameters for providers (e.g., gpt-5 rejects some params)
        litellm.drop_params = True

    def _setup_env(self, api_key: str, api_base: str | None, model: str) -> None:
        """Set environment variables based on detected provider."""
        spec = self._gateway or find_by_model(model)
        if not spec:
            return

        # Gateway/local overrides existing env; standard provider doesn't
        if self._gateway:
            os.environ[spec.env_key] = api_key
        else:
            os.environ.setdefault(spec.env_key, api_key)

        # Resolve env_extras placeholders:
        #   {api_key}  → user's API key
        #   {api_base} → user's api_base, falling back to spec.default_api_base
        effective_base = api_base or spec.default_api_base
        for env_name, env_val in spec.env_extras:
            resolved = env_val.replace("{api_key}", api_key)
            resolved = resolved.replace("{api_base}", effective_base)
            os.environ.setdefault(env_name, resolved)

    def _resolve_model(self, model: str) -> str:
        """Resolve model name by applying provider/gateway prefixes."""
        if self._gateway:
            # Gateway mode: apply gateway prefix, skip provider-specific prefixes
            prefix = self._gateway.litellm_prefix
            if self._gateway.strip_model_prefix:
                model = model.split("/")[-1]
            if prefix and not model.startswith(f"{prefix}/"):
                model = f"{prefix}/{model}"
            return model

        # Standard mode: auto-prefix for known providers
        spec = find_by_model(model)
        if spec and spec.litellm_prefix:
            if not any(model.startswith(s) for s in spec.skip_prefixes):
                model = f"{spec.litellm_prefix}/{model}"

        return model

    def _apply_model_overrides(self, model: str, kwargs: dict[str, Any]) -> None:
        """Apply model-specific parameter overrides from the registry."""
        model_lower = model.lower()
        spec = find_by_model(model)
        if spec:
            for pattern, overrides in spec.model_overrides:
                if pattern in model_lower:
                    kwargs.update(overrides)
                    return

    # ── Model fallback rotation ──────────────────────────────────────

    def _get_current_model(self, requested_model: str) -> str:
        """Get the current model, applying fallback rotation if models are configured.

        If fallback_models is empty, returns the requested model unchanged.
        Otherwise returns the model at the current rotation index.
        """
        if not self._fallback_models:
            return requested_model
        return self._fallback_models[self._model_index % len(self._fallback_models)]

    def _rotate_model(self) -> None:
        """Rotate to next fallback model after failure."""
        if not self._fallback_models:
            return
        old_idx = self._model_index
        self._model_index = (self._model_index + 1) % len(self._fallback_models)
        self._last_rotation_time = _time.time()
        old_model = self._fallback_models[old_idx % len(self._fallback_models)]
        new_model = self._fallback_models[self._model_index]
        logger.warning(f"LLM fallback: rotated from {old_model} → {new_model}")

    def _record_failure(self, model: str) -> None:
        """Track consecutive failures per model."""
        self._model_failures[model] = self._model_failures.get(model, 0) + 1

    def set_primary_model(self, model: str) -> str:
        """Hot-swap the primary model. Returns previous model name.

        Resets fallback rotation to index 0 (the new primary) and clears
        all failure counters so the new model starts fresh.
        """
        old = self.default_model
        self.default_model = model
        if self._fallback_models:
            self._fallback_models[0] = model
            self._model_index = 0  # Reset rotation to new primary
        self._model_failures.clear()
        return old

    # ── Model recovery ───────────────────────────────────────────

    def _should_try_recovery(self) -> bool:
        """Check whether we should probe the primary model to see if it recovered.

        Returns True when ALL conditions are met:
        1. Fallback list has 2+ models
        2. Not already on primary (index != 0)
        3. RECOVERY_COOLDOWN elapsed since last recovery attempt
        4. RECOVERY_COOLDOWN elapsed since last rotation (don't probe right after failing)
        """
        if not self._fallback_models or len(self._fallback_models) < 2:
            return False
        if self._model_index == 0:
            return False
        now = _time.time()
        if now - self._last_recovery_attempt < RECOVERY_COOLDOWN:
            return False
        if now - self._last_rotation_time < RECOVERY_COOLDOWN:
            return False
        return True

    async def _attempt_recovery(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse | None:
        """Probe the primary model to see if it has recovered.

        Uses the real chat request as the ping — no wasted API calls.
        On success: snap back to primary, clear failures, return response.
        On failure: stay on current fallback, return None. Silent failure —
        no _record_failure(), no _rotate_model(). Try again after cooldown.
        """
        self._last_recovery_attempt = _time.time()
        primary_model = self._fallback_models[0]
        current_model = self._fallback_models[self._model_index]

        logger.info(
            f"LLM recovery: probing primary {primary_model} "
            f"(currently on {current_model})"
        )

        try:
            response = await self._attempt_chat(
                primary_model, messages, tools, max_tokens, temperature,
            )
            # Success — snap back to primary
            self._model_index = 0
            self._model_failures[primary_model] = 0
            self._last_rotation_time = 0.0
            logger.info(
                f"LLM recovery: {primary_model} is back! "
                f"Switched from {current_model}."
            )
            return response

        except (asyncio.TimeoutError, Exception) as e:
            # Silent failure — stay on fallback, try again later
            logger.info(
                f"LLM recovery: {primary_model} still down "
                f"({type(e).__name__}). Staying on {current_model}. "
                f"Will retry in {RECOVERY_COOLDOWN}s."
            )
            return None

    async def _attempt_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Make a single LLM call with timeout. Raises on failure."""
        resolved = self._resolve_model(model)

        kwargs: dict[str, Any] = {
            "model": resolved,
            "messages": messages,
            "max_tokens": max(1, max_tokens),
            "temperature": temperature,
        }

        self._apply_model_overrides(resolved, kwargs)

        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base
        if self.extra_headers:
            kwargs["extra_headers"] = self.extra_headers
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        # Timeout prevents indefinite blocking on provider issues
        response = await asyncio.wait_for(
            acompletion(**kwargs),
            timeout=self._timeout,
        )
        return self._parse_response(response)

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """
        Send a chat completion request via LiteLLM with fallback rotation.

        On timeout or error: rotates to the next fallback model and retries
        once. If the retry also fails, returns an error response.

        Args:
            messages: List of message dicts with 'role' and 'content'.
            tools: Optional list of tool definitions in OpenAI format.
            model: Model identifier (e.g., 'anthropic/claude-sonnet-4-5').
            max_tokens: Maximum tokens in response.
            temperature: Sampling temperature.

        Returns:
            LLMResponse with content and/or tool calls.
        """
        requested = model or self.default_model
        current_model = self._get_current_model(requested)

        # Recovery probe: if on a fallback model and cooldown elapsed,
        # try the primary model first. If it works, snap back.
        if self._should_try_recovery():
            recovery_response = await self._attempt_recovery(
                messages, tools, max_tokens, temperature,
            )
            if recovery_response is not None:
                return recovery_response
            # Recovery failed — fall through to current fallback
            current_model = self._get_current_model(requested)

        # First attempt with current model
        try:
            result = await self._attempt_chat(
                current_model, messages, tools, max_tokens, temperature,
            )
            # Clear failure counter on success (matches daemon pattern)
            self._model_failures[current_model] = 0
            return result

        except asyncio.TimeoutError:
            logger.warning(
                f"LLM timeout after {self._timeout}s on {current_model}"
            )
            self._record_failure(current_model)
            self._rotate_model()

        except Exception as e:
            logger.warning(
                f"LLM error on {current_model}: {e}"
            )
            self._record_failure(current_model)
            self._rotate_model()

        # Retry once with the next fallback model
        fallback_model = self._get_current_model(requested)
        try:
            logger.info(f"LLM fallback retry with {fallback_model}")
            result = await self._attempt_chat(
                fallback_model, messages, tools, max_tokens, temperature,
            )
            self._model_failures[fallback_model] = 0
            return result

        except asyncio.TimeoutError:
            logger.error(
                f"LLM fallback also timed out on {fallback_model}"
            )
            self._record_failure(fallback_model)
            self._rotate_model()
            return LLMResponse(
                content=f"Error calling LLM: timeout on both {current_model} and {fallback_model}",
                finish_reason="error",
            )

        except Exception as e:
            logger.error(
                f"LLM fallback also failed on {fallback_model}: {e}"
            )
            self._record_failure(fallback_model)
            self._rotate_model()
            return LLMResponse(
                content=f"Error calling LLM: {str(e)}",
                finish_reason="error",
            )

    def _parse_response(self, response: Any) -> LLMResponse:
        """Parse LiteLLM response into our standard format."""
        choice = response.choices[0]
        message = choice.message

        tool_calls = []
        if hasattr(message, "tool_calls") and message.tool_calls:
            for tc in message.tool_calls:
                # Parse arguments from JSON string if needed
                args = tc.function.arguments
                if isinstance(args, str):
                    args = json_repair.loads(args)

                tool_calls.append(ToolCallRequest(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=args,
                ))

        usage = {}
        if hasattr(response, "usage") and response.usage:
            usage = {
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            }

        reasoning_content = getattr(message, "reasoning_content", None)

        return LLMResponse(
            content=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason or "stop",
            usage=usage,
            reasoning_content=reasoning_content,
        )

    def get_default_model(self) -> str:
        """Get the default model."""
        return self.default_model
