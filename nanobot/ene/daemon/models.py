"""Data models for the subconscious daemon.

The daemon pre-processes every message before Ene sees it,
providing LLM-powered classification, security analysis, and
message sanitization using free models.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Classification(str, Enum):
    """Message classification result from daemon."""
    RESPOND = "respond"
    CONTEXT = "context"
    DROP = "drop"


@dataclass
class SecurityFlag:
    """A security concern detected by the daemon."""
    type: str       # "jailbreak", "injection", "impersonation", "manipulation"
    severity: str   # "low", "medium", "high"
    description: str
    stripped: bool = False  # True if the daemon removed the offending content


@dataclass
class DaemonResult:
    """Result from daemon processing a single message."""

    # ── Classification ─────────────────────────────────────────────────
    classification: Classification = Classification.CONTEXT
    confidence: float = 0.8
    classification_reason: str = ""

    # ── Security ───────────────────────────────────────────────────────
    security_flags: list[SecurityFlag] = field(default_factory=list)
    sanitized_content: str | None = None  # Cleaned message (None = use original)

    # ── Analysis ───────────────────────────────────────────────────────
    implicit_ene_reference: bool = False  # Talking ABOUT Ene without @mention
    topic_summary: str = ""              # Brief topic for Ene's context
    emotional_tone: str = ""             # "friendly", "hostile", "neutral", etc.

    # ── Metadata ───────────────────────────────────────────────────────
    model_used: str = ""
    latency_ms: int = 0
    fallback_used: bool = False  # True if hardcoded fallback was used

    @property
    def has_security_flags(self) -> bool:
        return len(self.security_flags) > 0

    @property
    def should_auto_mute(self) -> bool:
        """High-severity security flags should trigger auto-mute."""
        return any(f.severity == "high" for f in self.security_flags)


# ── Free model configuration ──────────────────────────────────────────

# Models to rotate through when primary model fails or is rate-limited
DEFAULT_FREE_MODELS = [
    "meta-llama/llama-4-maverick:free",
    "qwen/qwen3-30b-a3b:free",
    "google/gemma-3-27b-it:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
]
