"""Ene security: identity verification, impersonation detection, muting, rate limiting.

All security constants and pure functions extracted from loop.py for modularity.
See docs/WHITELIST.md decisions X1-X7 for reasoning.
"""

import re
import time
from typing import Any

from loguru import logger


# === Hardcoded identity — the trust root (WHITELIST X1) ===
DAD_IDS: set[str] = {"telegram:8559611823", "discord:1175414972482846813"}
RESTRICTED_TOOLS: set[str] = {
    "exec", "write_file", "edit_file", "read_file", "list_dir",
    "spawn", "cron", "view_metrics", "view_experiments", "view_module",
}

# Word-boundary match to avoid false positives ("generic", "scene", etc.)
ENE_PATTERN = re.compile(r"\bene\b", re.IGNORECASE)

# Dad's known display names (lowercased) for impersonation detection
DAD_DISPLAY_NAMES: set[str] = {
    "iitai", "litai", "言いたい", "iitai / 言いたい", "litai / 言いたい",
}
_CONFUSABLE_PAIRS = str.maketrans("lI", "Il")  # l↔I swap detection

# Content-level impersonation patterns
_DAD_VOICE_PATTERNS = re.compile(
    r'(?:iitai|litai|dad|baba|abba|父)\s*(?:says?|said|:|\s*-\s*["\'])',
    re.IGNORECASE,
)
_DAD_RAW_IDS: set[str] = {pid.split(":", 1)[1] for pid in DAD_IDS}
_DAD_ID_CONTENT_PATTERN = re.compile(
    r'(?:@?\s*(?:' + '|'.join(re.escape(rid) for rid in _DAD_RAW_IDS) + r'))\s*[:\-]',
)


# ---------------------------------------------------------------------------
# Pure functions — no instance state
# ---------------------------------------------------------------------------

def is_dad_impersonation(display_name: str, caller_id: str) -> bool:
    """Check if a display name looks like Dad's but the ID doesn't match."""
    if caller_id in DAD_IDS:
        return False
    name_lower = display_name.lower().strip()
    if name_lower in DAD_DISPLAY_NAMES:
        return True
    swapped = name_lower.translate(_CONFUSABLE_PAIRS)
    if swapped in DAD_DISPLAY_NAMES:
        return True
    for dad_name in DAD_DISPLAY_NAMES:
        if dad_name in name_lower or dad_name in swapped:
            return True
    return False


def has_content_impersonation(content: str, caller_id: str) -> bool:
    """Check if message content claims to relay Dad's words (from a non-Dad sender)."""
    if caller_id in DAD_IDS:
        return False
    if _DAD_VOICE_PATTERNS.search(content):
        return True
    if _DAD_ID_CONTENT_PATTERN.search(content):
        return True
    return False


def sanitize_dad_ids(content: str, caller_id: str) -> str:
    """Strip Dad's raw platform IDs from non-Dad message content."""
    if caller_id in DAD_IDS:
        return content
    result = content
    for raw_id in _DAD_RAW_IDS:
        if raw_id in result:
            result = result.replace(raw_id, "[someone's_id]")
    return result


def is_rate_limited(
    timestamps: list[float],
    window: float,
    max_count: int,
    caller_id: str,
) -> tuple[bool, list[float]]:
    """Check if a user is sending too fast. Returns (limited, pruned_timestamps).

    Dad is never rate-limited.
    """
    if caller_id in DAD_IDS:
        return False, timestamps
    now = time.time()
    pruned = [t for t in timestamps if now - t < window]
    pruned.append(now)
    return len(pruned) > max_count, pruned


def is_muted(muted_users: dict[str, float], caller_id: str) -> bool:
    """Check if a user is currently muted. Auto-expires."""
    if caller_id in DAD_IDS:
        return False
    expires = muted_users.get(caller_id)
    if expires is None:
        return False
    if time.time() > expires:
        muted_users.pop(caller_id, None)  # pop avoids KeyError if removed concurrently
        return False
    return True


def record_suspicious(
    jailbreak_scores: dict[str, list[float]],
    caller_id: str,
    reason: str,
    module_registry: Any = None,
) -> None:
    """Record a suspicious action for jailbreak detection + trust violation."""
    if caller_id in DAD_IDS:
        return
    scores = jailbreak_scores.get(caller_id, [])
    scores.append(time.time())
    jailbreak_scores[caller_id] = scores

    # Bridge to trust system
    if module_registry is not None:
        try:
            social = module_registry.get_module("social")
            if social and hasattr(social, "registry") and social.registry:
                social.registry.record_violation(caller_id, reason, severity=0.10)
        except Exception:
            pass


def check_auto_mute(
    jailbreak_scores: dict[str, list[float]],
    muted_users: dict[str, float],
    caller_id: str,
    author_name: str,
    jailbreak_window: float,
    jailbreak_threshold: int,
    mute_duration: float,
    module_registry: Any = None,
) -> bool:
    """Auto-mute if enough suspicious actions. Returns True if muted."""
    if caller_id in DAD_IDS:
        return False
    if is_muted(muted_users, caller_id):
        return False

    # Check trust tier — don't auto-mute familiar+ users
    if module_registry is not None:
        try:
            social = module_registry.get_module("social")
            if social and hasattr(social, "registry") and social.registry:
                person = social.registry.get_by_platform_id(caller_id)
                if person:
                    from nanobot.ene.social.trust import TIER_ORDER
                    try:
                        tier_idx = TIER_ORDER.index(person.trust.tier)
                        if tier_idx >= TIER_ORDER.index("familiar"):
                            return False
                    except ValueError:
                        pass
        except Exception:
            pass

    now = time.time()
    scores = jailbreak_scores.get(caller_id, [])
    scores = [t for t in scores if now - t < jailbreak_window]
    jailbreak_scores[caller_id] = scores

    if len(scores) >= jailbreak_threshold:
        muted_users[caller_id] = now + mute_duration
        jailbreak_scores[caller_id] = []
        logger.warning(
            f"Auto-muted {author_name} ({caller_id}) for {mute_duration // 60} min "
            f"({len(scores)} suspicious actions in {jailbreak_window}s)"
        )
        return True
    return False


# ---------------------------------------------------------------------------
# MuteUserTool — Ene's self-service mute capability
# ---------------------------------------------------------------------------

class MuteUserTool:
    """Let Ene mute annoying users. Not a restricted tool — Ene can use it freely."""

    def __init__(self, muted_users: dict, module_registry: Any):
        self._muted = muted_users
        self._registry = module_registry

    @property
    def name(self) -> str:
        return "mute_user"

    @property
    def description(self) -> str:
        return "Mute someone who's annoying you for 1-10 minutes. They'll get a canned response instead of your attention."

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": "The person's display name or username",
                },
                "minutes": {
                    "type": "integer",
                    "description": "How long to mute them (1-30 minutes, default 5)",
                },
            },
            "required": ["username"],
        }

    def validate_params(self, params: dict) -> list[str]:
        errors = []
        if "username" not in params:
            errors.append("missing required username")
        return errors

    def to_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    async def execute(self, **kwargs) -> str:
        username = str(kwargs.get("username", "")).strip()
        minutes = min(max(int(kwargs.get("minutes", 5)), 1), 30)

        if not username:
            return "Who am I muting? Give me a name."

        social = self._registry.get_module("social")
        person = None
        platform_id = None

        if social and hasattr(social, "registry") and social.registry:
            registry = social.registry
            person = registry.find_by_name(username)
            if not person:
                name_lower = username.lower()
                for p in registry.get_all():
                    if (name_lower in p.display_name.lower()
                            or any(name_lower in a.lower() for a in p.aliases)
                            or any(name_lower in pid_obj.username.lower()
                                   for pid_obj in p.platform_ids.values())):
                        person = p
                        break

            if person:
                for pid_key in person.platform_ids:
                    platform_id = pid_key
                    break

        if not person or not platform_id:
            return f"I don't know anyone called '{username}'. Can't mute someone I don't recognize."

        if platform_id in DAD_IDS:
            return "Nice try, but I'm not muting Dad."

        for pid_key in person.platform_ids:
            self._muted[pid_key] = time.time() + (minutes * 60)
        display = person.display_name
        return f"Done. {display} is muted for {minutes} minutes."
