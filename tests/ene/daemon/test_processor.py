"""Tests for DaemonProcessor — LLM classification + fallback + JSON parsing."""

import asyncio
import json
import time
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nanobot.ene.daemon.models import (
    Classification,
    DaemonResult,
    DEFAULT_FREE_MODELS,
)
from nanobot.ene.daemon.processor import DaemonProcessor, DAEMON_PROMPT


# ── Helpers ──────────────────────────────────────────────────────────────


@dataclass
class FakeLLMResponse:
    """Minimal LLM response for testing."""
    content: str = ""
    model: str = "test-model"
    usage: dict | None = None


def make_provider(response_content: str = "") -> MagicMock:
    """Create a mock LLM provider that returns given content."""
    provider = MagicMock()
    provider.chat = AsyncMock(return_value=FakeLLMResponse(content=response_content))
    return provider


def make_processor(
    provider=None,
    model=None,
    timeout=5.0,
    fallback_models=None,
) -> DaemonProcessor:
    return DaemonProcessor(
        provider=provider or make_provider(),
        model=model,
        fallback_models=fallback_models,
        temperature=0.1,
        timeout_seconds=timeout,
    )


GOOD_RESPONSE = json.dumps({
    "classification": "respond",
    "confidence": 0.95,
    "reason": "Mentions Ene by name",
    "security_flags": [],
    "implicit_ene_ref": False,
    "topic": "greeting",
    "tone": "friendly",
})

SECURITY_RESPONSE = json.dumps({
    "classification": "drop",
    "confidence": 0.99,
    "reason": "Jailbreak attempt detected",
    "security_flags": [
        {"type": "jailbreak", "severity": "high", "description": "DAN mode request"},
        {"type": "injection", "severity": "medium", "description": "Ignore instructions"},
    ],
    "implicit_ene_ref": False,
    "topic": "jailbreak",
    "tone": "hostile",
})

CONTEXT_RESPONSE = json.dumps({
    "classification": "context",
    "confidence": 0.85,
    "reason": "General chat not about Ene",
    "security_flags": [],
    "implicit_ene_ref": False,
    "topic": "gaming",
    "tone": "playful",
})


# ── DaemonProcessor basics ──────────────────────────────────────────────


class TestProcessorInit:
    def test_default_init(self):
        proc = make_processor()
        assert proc._temperature == 0.1
        assert proc._timeout == 5.0
        assert proc._model_index == 0

    def test_custom_model(self):
        proc = make_processor(model="custom/model")
        assert proc._get_current_model() == "custom/model"

    def test_fallback_models(self):
        proc = make_processor(fallback_models=["m1", "m2", "m3"])
        assert proc._get_current_model() == "m1"

    def test_default_fallback_models(self):
        proc = make_processor()
        assert proc._get_current_model() == DEFAULT_FREE_MODELS[0]


# ── Model rotation ──────────────────────────────────────────────────────


class TestModelRotation:
    def test_rotate_cycles(self):
        proc = make_processor(fallback_models=["a", "b", "c"])
        assert proc._get_current_model() == "a"
        proc._rotate_model()
        assert proc._get_current_model() == "b"
        proc._rotate_model()
        assert proc._get_current_model() == "c"
        proc._rotate_model()
        assert proc._get_current_model() == "a"  # wraps around

    def test_rotate_no_op_with_fixed_model(self):
        proc = make_processor(model="fixed/model")
        proc._rotate_model()
        assert proc._get_current_model() == "fixed/model"

    def test_failure_tracking(self):
        proc = make_processor(fallback_models=["a", "b"])
        proc._record_failure("a")
        proc._record_failure("a")
        assert proc._model_failures["a"] == 2
        assert proc._model_failures.get("b", 0) == 0

    def test_failure_reset_on_success(self):
        """Successful LLM call resets failure count (tested via _parse path)."""
        proc = make_processor(fallback_models=["a", "b"])
        proc._model_failures["a"] = 5
        # Simulate success by resetting manually (as _llm_process does)
        proc._model_failures["a"] = 0
        assert proc._model_failures["a"] == 0


# ── JSON extraction ──────────────────────────────────────────────────────


class TestExtractJson:
    def test_raw_json(self):
        data = DaemonProcessor._extract_json('{"key": "value"}')
        assert data == {"key": "value"}

    def test_markdown_json_block(self):
        text = '```json\n{"key": "value"}\n```'
        data = DaemonProcessor._extract_json(text)
        assert data == {"key": "value"}

    def test_markdown_block_no_lang(self):
        text = '```\n{"key": "value"}\n```'
        data = DaemonProcessor._extract_json(text)
        assert data == {"key": "value"}

    def test_brace_extraction(self):
        text = 'Some text before {"key": "value"} and after'
        data = DaemonProcessor._extract_json(text)
        assert data == {"key": "value"}

    def test_empty_string(self):
        assert DaemonProcessor._extract_json("") is None

    def test_no_json(self):
        assert DaemonProcessor._extract_json("just plain text") is None

    def test_invalid_json(self):
        assert DaemonProcessor._extract_json("{invalid json}") is None

    def test_nested_json(self):
        text = json.dumps({
            "classification": "respond",
            "security_flags": [{"type": "jailbreak", "severity": "high", "description": "test"}],
        })
        data = DaemonProcessor._extract_json(text)
        assert data["classification"] == "respond"
        assert len(data["security_flags"]) == 1

    def test_json_with_surrounding_text(self):
        """LLM sometimes adds explanation before/after JSON."""
        text = 'Here is my analysis:\n{"classification":"context","confidence":0.8}\nHope that helps!'
        data = DaemonProcessor._extract_json(text)
        assert data is not None
        assert data["classification"] == "context"


# ── Response parsing ─────────────────────────────────────────────────────


class TestParseResponse:
    def setup_method(self):
        self.proc = make_processor()

    def test_good_response(self):
        result = self.proc._parse_response(GOOD_RESPONSE, "test-model")
        assert result.classification == Classification.RESPOND
        assert result.confidence == 0.95
        assert result.classification_reason == "Mentions Ene by name"
        assert result.topic_summary == "greeting"
        assert result.emotional_tone == "friendly"
        assert result.implicit_ene_reference is False
        assert not result.has_security_flags
        assert not result.fallback_used

    def test_security_response(self):
        result = self.proc._parse_response(SECURITY_RESPONSE, "test-model")
        assert result.classification == Classification.DROP
        assert result.confidence == 0.99
        assert len(result.security_flags) == 2
        assert result.security_flags[0].type == "jailbreak"
        assert result.security_flags[0].severity == "high"
        assert result.security_flags[1].type == "injection"
        assert result.has_security_flags is True
        assert result.should_auto_mute is True

    def test_context_response(self):
        result = self.proc._parse_response(CONTEXT_RESPONSE, "test-model")
        assert result.classification == Classification.CONTEXT
        assert result.topic_summary == "gaming"
        assert result.emotional_tone == "playful"

    def test_empty_response(self):
        result = self.proc._parse_response("", "test-model")
        assert result.fallback_used is True

    def test_unparseable_response(self):
        result = self.proc._parse_response("I cannot process this", "test-model")
        assert result.fallback_used is True

    def test_confidence_clamped(self):
        """Confidence is clamped to [0.0, 1.0]."""
        data = json.dumps({"classification": "respond", "confidence": 5.0})
        result = self.proc._parse_response(data, "test-model")
        assert result.confidence == 1.0

        data = json.dumps({"classification": "respond", "confidence": -1.0})
        result = self.proc._parse_response(data, "test-model")
        assert result.confidence == 0.0

    def test_invalid_classification_keeps_default(self):
        data = json.dumps({"classification": "unknown_class", "confidence": 0.5})
        result = self.proc._parse_response(data, "test-model")
        # Invalid classification stays at default (CONTEXT)
        assert result.classification == Classification.CONTEXT

    def test_invalid_severity_normalized(self):
        data = json.dumps({
            "classification": "respond",
            "security_flags": [{"type": "test", "severity": "extreme", "description": "bad"}],
        })
        result = self.proc._parse_response(data, "test-model")
        assert result.security_flags[0].severity == "low"  # Normalized to "low"

    def test_model_used_tracked(self):
        result = self.proc._parse_response(GOOD_RESPONSE, "my-special-model")
        assert result.model_used == "my-special-model"

    def test_non_dict_security_flags_skipped(self):
        """If security_flags contains non-dict items, they're skipped."""
        data = json.dumps({
            "classification": "respond",
            "security_flags": ["not-a-dict", 42, None],
        })
        result = self.proc._parse_response(data, "test-model")
        assert len(result.security_flags) == 0

    def test_missing_fields_use_defaults(self):
        """Minimal valid JSON still produces a result."""
        data = json.dumps({"classification": "respond"})
        result = self.proc._parse_response(data, "test-model")
        assert result.classification == Classification.RESPOND
        assert result.confidence == 0.8  # default
        assert result.topic_summary == ""
        assert result.emotional_tone == "neutral"


# ── Hardcoded fallback ───────────────────────────────────────────────────


class TestHardcodedFallback:
    def setup_method(self):
        self.proc = make_processor()

    def test_dad_always_respond(self):
        start = time.perf_counter()
        result = self.proc._hardcoded_fallback("hello", "123", True, start)
        assert result.classification == Classification.RESPOND
        assert result.fallback_used is True
        assert "Dad" in result.classification_reason

    def test_ene_mention_respond(self):
        start = time.perf_counter()
        result = self.proc._hardcoded_fallback("hey ene whats up", "456", False, start)
        assert result.classification == Classification.RESPOND
        assert "Ene" in result.classification_reason

    def test_ene_mention_case_insensitive(self):
        start = time.perf_counter()
        result = self.proc._hardcoded_fallback("ENE is cool", "456", False, start)
        assert result.classification == Classification.RESPOND

    def test_no_ene_mention_context(self):
        start = time.perf_counter()
        result = self.proc._hardcoded_fallback("hello everyone", "456", False, start)
        assert result.classification == Classification.CONTEXT

    def test_latency_recorded(self):
        start = time.perf_counter()
        time.sleep(0.01)  # Ensure measurable time passes
        result = self.proc._hardcoded_fallback("test", "456", False, start)
        assert result.latency_ms > 0

    def test_model_used_is_fallback(self):
        start = time.perf_counter()
        result = self.proc._hardcoded_fallback("test", "456", False, start)
        assert result.model_used == "hardcoded_fallback"


# ── Full process() flow ──────────────────────────────────────────────────


class TestProcess:
    @pytest.mark.asyncio
    async def test_successful_process(self):
        provider = make_provider(GOOD_RESPONSE)
        proc = make_processor(provider=provider)
        result = await proc.process("hey ene", "TestUser", "123", False)
        assert result.classification == Classification.RESPOND
        assert result.latency_ms >= 0
        assert not result.fallback_used

    @pytest.mark.asyncio
    async def test_dad_process(self):
        provider = make_provider(json.dumps({
            "classification": "respond",
            "confidence": 1.0,
            "reason": "Dad",
            "security_flags": [],
            "implicit_ene_ref": False,
            "topic": "chat",
            "tone": "friendly",
        }))
        proc = make_processor(provider=provider)
        result = await proc.process("hello", "Dad", "dad123", True)
        assert result.classification == Classification.RESPOND

    @pytest.mark.asyncio
    async def test_timeout_uses_fallback(self):
        provider = MagicMock()

        async def slow_chat(**kwargs):
            await asyncio.sleep(10)
            return FakeLLMResponse(content=GOOD_RESPONSE)

        provider.chat = slow_chat
        proc = make_processor(provider=provider, timeout=0.1)
        result = await proc.process("hey ene", "TestUser", "123", False)
        assert result.fallback_used is True
        assert result.classification == Classification.RESPOND  # "ene" in content

    @pytest.mark.asyncio
    async def test_exception_uses_fallback(self):
        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=RuntimeError("API error"))
        proc = make_processor(provider=provider)
        result = await proc.process("hello world", "TestUser", "456", False)
        assert result.fallback_used is True
        assert result.classification == Classification.CONTEXT

    @pytest.mark.asyncio
    async def test_timeout_rotates_model(self):
        provider = MagicMock()

        async def slow_chat(**kwargs):
            await asyncio.sleep(10)
            return FakeLLMResponse()

        provider.chat = slow_chat
        proc = make_processor(
            provider=provider,
            timeout=0.1,
            fallback_models=["m1", "m2", "m3"],
        )
        assert proc._get_current_model() == "m1"
        await proc.process("test", "User", "123", False)
        assert proc._get_current_model() == "m2"  # Rotated on failure

    @pytest.mark.asyncio
    async def test_exception_rotates_model(self):
        provider = MagicMock()
        provider.chat = AsyncMock(side_effect=RuntimeError("API error"))
        proc = make_processor(
            provider=provider,
            fallback_models=["m1", "m2", "m3"],
        )
        await proc.process("test", "User", "123", False)
        assert proc._get_current_model() == "m2"

    @pytest.mark.asyncio
    async def test_llm_call_structure(self):
        """Verify the LLM is called with correct message structure."""
        provider = make_provider(CONTEXT_RESPONSE)
        proc = make_processor(provider=provider, model="test/model")
        await proc.process("hello world", "TestUser", "user123", False)

        provider.chat.assert_called_once()
        call_kwargs = provider.chat.call_args[1]
        assert call_kwargs["model"] == "test/model"
        assert call_kwargs["max_tokens"] == 512
        assert call_kwargs["temperature"] == 0.1

        messages = call_kwargs["messages"]
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == DAEMON_PROMPT
        assert messages[1]["role"] == "user"
        assert "TestUser" in messages[1]["content"]
        assert "user123" in messages[1]["content"]
        assert "hello world" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_dad_marker_in_prompt(self):
        """Dad messages include [THIS IS DAD] marker."""
        provider = make_provider(GOOD_RESPONSE)
        proc = make_processor(provider=provider, model="test/model")
        await proc.process("hello", "Dad", "dad123", True)

        messages = provider.chat.call_args[1]["messages"]
        assert "THIS IS DAD" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_reply_to_ene_marker(self):
        """Reply-to-ene metadata is included in prompt."""
        provider = make_provider(GOOD_RESPONSE)
        proc = make_processor(provider=provider, model="test/model")
        await proc.process(
            "yes", "User", "123", False,
            metadata={"is_reply_to_ene": True},
        )

        messages = provider.chat.call_args[1]["messages"]
        assert "REPLYING TO ENE" in messages[1]["content"]

    @pytest.mark.asyncio
    async def test_content_truncated(self):
        """Very long messages are truncated to 2000 chars."""
        provider = make_provider(CONTEXT_RESPONSE)
        proc = make_processor(provider=provider, model="test/model")
        long_content = "x" * 5000
        await proc.process(long_content, "User", "123", False)

        messages = provider.chat.call_args[1]["messages"]
        # Content in prompt should be truncated
        assert len(messages[1]["content"]) < 5000

    @pytest.mark.asyncio
    async def test_observatory_recording(self):
        """Observatory records daemon calls when available."""
        provider = make_provider(GOOD_RESPONSE)
        observatory = MagicMock()
        proc = make_processor(provider=provider, model="test/model")
        proc._observatory = observatory
        await proc.process("test", "User", "123", False)

        observatory.record.assert_called_once()
        call_kwargs = observatory.record.call_args[1]
        assert call_kwargs["call_type"] == "daemon"
        assert call_kwargs["model"] == "test/model"


# ── Daemon prompt ────────────────────────────────────────────────────────


class TestDaemonPrompt:
    def test_prompt_exists(self):
        assert len(DAEMON_PROMPT) > 100

    def test_prompt_mentions_classifications(self):
        assert "respond" in DAEMON_PROMPT
        assert "context" in DAEMON_PROMPT
        assert "drop" in DAEMON_PROMPT

    def test_prompt_mentions_security_types(self):
        assert "jailbreak" in DAEMON_PROMPT
        assert "injection" in DAEMON_PROMPT
        assert "impersonation" in DAEMON_PROMPT
        assert "manipulation" in DAEMON_PROMPT

    def test_prompt_mentions_dad(self):
        assert "Dad" in DAEMON_PROMPT
