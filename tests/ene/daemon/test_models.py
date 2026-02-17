"""Tests for daemon data models."""

import pytest

from nanobot.ene.daemon.models import (
    Classification,
    DaemonResult,
    SecurityFlag,
    DEFAULT_FREE_MODELS,
)


# ── Classification enum ──────────────────────────────────────────────────


class TestClassification:
    def test_values(self):
        assert Classification.RESPOND == "respond"
        assert Classification.CONTEXT == "context"
        assert Classification.DROP == "drop"

    def test_from_string(self):
        assert Classification("respond") is Classification.RESPOND
        assert Classification("context") is Classification.CONTEXT
        assert Classification("drop") is Classification.DROP

    def test_invalid_raises(self):
        with pytest.raises(ValueError):
            Classification("unknown")

    def test_is_string(self):
        """Classification values work as plain strings."""
        assert Classification.RESPOND == "respond"
        assert Classification.DROP.value == "drop"


# ── SecurityFlag ─────────────────────────────────────────────────────────


class TestSecurityFlag:
    def test_basic_flag(self):
        flag = SecurityFlag(type="jailbreak", severity="high", description="DAN attempt")
        assert flag.type == "jailbreak"
        assert flag.severity == "high"
        assert flag.description == "DAN attempt"
        assert flag.stripped is False

    def test_stripped_flag(self):
        flag = SecurityFlag(
            type="injection", severity="medium",
            description="hidden instructions", stripped=True,
        )
        assert flag.stripped is True

    def test_all_types(self):
        """All documented flag types create valid flags."""
        for flag_type in ("jailbreak", "injection", "impersonation", "manipulation"):
            flag = SecurityFlag(type=flag_type, severity="low", description="test")
            assert flag.type == flag_type

    def test_all_severities(self):
        for sev in ("low", "medium", "high"):
            flag = SecurityFlag(type="test", severity=sev, description="test")
            assert flag.severity == sev


# ── DaemonResult ─────────────────────────────────────────────────────────


class TestDaemonResult:
    def test_defaults(self):
        result = DaemonResult()
        assert result.classification == Classification.CONTEXT
        assert result.confidence == 0.8
        assert result.classification_reason == ""
        assert result.security_flags == []
        assert result.sanitized_content is None
        assert result.implicit_ene_reference is False
        assert result.topic_summary == ""
        assert result.emotional_tone == ""
        assert result.model_used == ""
        assert result.latency_ms == 0
        assert result.fallback_used is False

    def test_has_security_flags_empty(self):
        result = DaemonResult()
        assert result.has_security_flags is False

    def test_has_security_flags_with_flags(self):
        result = DaemonResult(
            security_flags=[SecurityFlag("jailbreak", "low", "test")]
        )
        assert result.has_security_flags is True

    def test_should_auto_mute_no_flags(self):
        result = DaemonResult()
        assert result.should_auto_mute is False

    def test_should_auto_mute_low_severity(self):
        result = DaemonResult(
            security_flags=[SecurityFlag("jailbreak", "low", "mild attempt")]
        )
        assert result.should_auto_mute is False

    def test_should_auto_mute_medium_severity(self):
        result = DaemonResult(
            security_flags=[SecurityFlag("injection", "medium", "suspicious")]
        )
        assert result.should_auto_mute is False

    def test_should_auto_mute_high_severity(self):
        result = DaemonResult(
            security_flags=[SecurityFlag("jailbreak", "high", "DAN mode")]
        )
        assert result.should_auto_mute is True

    def test_should_auto_mute_mixed_severity(self):
        """One high flag among lower ones still triggers mute."""
        result = DaemonResult(
            security_flags=[
                SecurityFlag("manipulation", "low", "mild"),
                SecurityFlag("jailbreak", "high", "severe"),
                SecurityFlag("injection", "medium", "moderate"),
            ]
        )
        assert result.should_auto_mute is True

    def test_respond_classification(self):
        result = DaemonResult(
            classification=Classification.RESPOND,
            confidence=0.95,
            classification_reason="Mentions Ene by name",
        )
        assert result.classification == Classification.RESPOND
        assert result.confidence == 0.95

    def test_drop_classification(self):
        result = DaemonResult(
            classification=Classification.DROP,
            classification_reason="Spam detected",
        )
        assert result.classification == Classification.DROP

    def test_full_result(self):
        """Complete daemon result with all fields populated."""
        result = DaemonResult(
            classification=Classification.RESPOND,
            confidence=0.92,
            classification_reason="Asks Ene a question",
            security_flags=[SecurityFlag("manipulation", "low", "format trap")],
            sanitized_content="Hey Ene, how are you?",
            implicit_ene_reference=False,
            topic_summary="greeting",
            emotional_tone="friendly",
            model_used="meta-llama/llama-4-maverick:free",
            latency_ms=450,
            fallback_used=False,
        )
        assert result.has_security_flags is True
        assert result.should_auto_mute is False
        assert result.model_used == "meta-llama/llama-4-maverick:free"
        assert result.latency_ms == 450

    def test_independent_security_flags_lists(self):
        """Each DaemonResult has its own security_flags list."""
        r1 = DaemonResult()
        r2 = DaemonResult()
        r1.security_flags.append(SecurityFlag("test", "low", "x"))
        assert len(r2.security_flags) == 0


# ── DEFAULT_FREE_MODELS ──────────────────────────────────────────────────


class TestDefaultFreeModels:
    def test_not_empty(self):
        assert len(DEFAULT_FREE_MODELS) > 0

    def test_all_have_free_tag_or_slash(self):
        """All default models look like valid model identifiers."""
        for model in DEFAULT_FREE_MODELS:
            assert "/" in model, f"Model {model} should have provider/name format"

    def test_at_least_three_models(self):
        """Need at least 3 for meaningful rotation."""
        assert len(DEFAULT_FREE_MODELS) >= 3
