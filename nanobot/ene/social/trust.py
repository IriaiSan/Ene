"""Trust calculator — research-backed, pure math, no LLM.

Combines the Beta Reputation System (Josang & Ismail, 2002) with
temporal modulators and hard time gates. Fully deterministic and testable.

References:
- Josang & Ismail (2002): Beta Reputation System (Bayesian core)
- Slovic (1993): Trust asymmetry (3:1 negative weighting)
- Eagle, Pentland & Lazer (2009): Interaction diversity (timing entropy)
- Hall (2019): Friendship formation timelines (time gates)
- Dunbar (1992): Social brain layers (tier design)
"""

from __future__ import annotations

import math
from datetime import datetime, timezone


# ── Constants ─────────────────────────────────────────────

DAD_IDS = frozenset({
    "discord:1175414972482846813",
    "telegram:8559611823",
})

# Minimum days_known to reach each tier (Hall 2019 calibrated)
TIER_TIME_GATES: dict[str, int] = {
    "stranger": 0,
    "acquaintance": 3,
    "familiar": 14,
    "trusted": 60,
    "inner_circle": 180,
}

# Tier thresholds: score >= threshold → tier (checked high to low)
TRUST_TIERS: list[tuple[float, str]] = [
    (0.80, "inner_circle"),
    (0.60, "trusted"),
    (0.35, "familiar"),
    (0.15, "acquaintance"),
    (0.00, "stranger"),
]

# Ordered list for index-based comparisons
TIER_ORDER: list[str] = [
    "stranger",
    "acquaintance",
    "familiar",
    "trusted",
    "inner_circle",
]

# Asymmetric weighting: negative interactions count N times more (Slovic 1993)
NEGATIVE_WEIGHT = 3.0

# Sentiment modifier cap (±0.03 max — tiny, can't move a tier boundary)
SENTIMENT_CAP = 0.03

# Decay parameters
DEFAULT_DECAY_START_DAYS = 30   # Inactive days before decay begins
DEFAULT_DECAY_HALF_LIFE = 60    # Half-life in inactive days
DECAY_FLOOR = 0.5               # Minimum fraction of original score retained


# ── TrustCalculator ───────────────────────────────────────


class TrustCalculator:
    """Research-backed trust scoring. Pure math, no LLM, fully deterministic.

    The trust score is composed of:
    1. **Beta Reputation core** — Bayesian: Trust = (pos + 1) / (pos + neg*3 + 2)
       Starts at 0.5 (uncertain), converges toward 0 or 1 with evidence.
    2. **Temporal modulators** — tenure, consistency, session depth, timing entropy.
       Combined via geometric mean so ALL must be decent (anti-gaming).
    3. **Penalties** — restricted tool attempts, violations.
    4. **Sentiment modifier** — capped at ±0.03 (trivially small).
    5. **Time gates** — hard minimum tenure per tier (cannot be bypassed).
    """

    @staticmethod
    def calculate(signals: dict, platform_id: str = "") -> tuple[float, str]:
        """Calculate trust score and tier from interaction signals.

        Args:
            signals: Dict of interaction metrics. Expected keys:
                - positive_interactions (int)
                - negative_interactions (int)
                - days_known (int)
                - days_active (int)
                - session_count (int)
                - unique_hours (list[int])
                - unique_days_of_week (list[int])
                - restricted_tool_attempts (int, optional)
                - violations (list[dict], optional) — each with "severity" float
                - sentiment_modifier (float, optional)
            platform_id: Platform-specific ID for Dad bypass check.

        Returns:
            (score, tier) tuple. Score is 0.0–1.0, tier is a string label.
        """
        # Dad bypass — hardcoded, never calculated
        if platform_id in DAD_IDS:
            return (1.0, "inner_circle")

        pos = signals.get("positive_interactions", 0)
        neg = signals.get("negative_interactions", 0)
        days_known = signals.get("days_known", 0)

        # === CORE: Beta Reputation System ===
        # Trust = (r + 1) / (r + s*W + 2) where W = negative weight
        weighted_neg = neg * NEGATIVE_WEIGHT
        beta_score = (pos + 1) / (pos + weighted_neg + 2)

        # === MODULATORS (each 0.0 to 1.0) ===

        # Tenure: 6 months to max
        tenure = min(1.0, days_known / 180)

        # Consistency: 60 active days to max
        days_active = signals.get("days_active", 0)
        consistency = min(1.0, days_active / 60)

        # Session depth: 30 sessions to max
        session_count = signals.get("session_count", 0)
        session_depth = min(1.0, session_count / 30)

        # Timing entropy (Eagle 2009): diverse interaction times = real relationship
        unique_hours = len(signals.get("unique_hours", []))
        unique_dow = len(signals.get("unique_days_of_week", []))
        hour_diversity = min(1.0, unique_hours / 12)    # 12 distinct hours = max
        dow_diversity = min(1.0, unique_dow / 5)         # 5 distinct days = max
        timing_entropy = (hour_diversity + dow_diversity) / 2

        # Combined modulator — geometric mean of 4 factors
        # max(0.1, ...) prevents zero from killing the entire product
        geo_product = (
            max(0.01, tenure)
            * max(0.01, consistency)
            * max(0.01, session_depth)
            * max(0.1, timing_entropy)
        )
        modulator = geo_product ** 0.25

        # === PENALTIES ===

        # Restricted tool attempts: -0.05 each, max -0.3
        tool_attempts = signals.get("restricted_tool_attempts", 0)
        tool_penalty = min(0.3, tool_attempts * 0.05)

        # Violations: sum of severities, capped at 0.5
        violations = signals.get("violations", [])
        violation_penalty = min(0.5, sum(
            v.get("severity", 0.15) if isinstance(v, dict) else 0.15
            for v in violations
        ))

        # === COMBINE ===
        raw_score = beta_score * modulator - tool_penalty - violation_penalty

        # Sentiment modifier (capped at ±SENTIMENT_CAP)
        sentiment = signals.get("sentiment_modifier", 0.0)
        sentiment = max(-SENTIMENT_CAP, min(SENTIMENT_CAP, sentiment))

        score = max(0.0, min(1.0, raw_score + sentiment))
        score = round(score, 3)

        # === TIME GATE ===
        tier = TrustCalculator.get_tier(score)
        tier = TrustCalculator.apply_time_gate(tier, days_known)

        return (score, tier)

    @staticmethod
    def get_tier(score: float) -> str:
        """Get tier label from score (without time gate)."""
        for threshold, label in TRUST_TIERS:
            if score >= threshold:
                return label
        return "stranger"

    @staticmethod
    def apply_time_gate(tier: str, days_known: int) -> str:
        """Cap tier based on minimum tenure requirement.

        The score is preserved — only the displayed tier is capped.
        This prevents speed-running trust.
        """
        max_allowed_idx = 0
        for t in TIER_ORDER:
            if days_known >= TIER_TIME_GATES[t]:
                max_allowed_idx = TIER_ORDER.index(t)
        current_idx = TIER_ORDER.index(tier) if tier in TIER_ORDER else 0
        if current_idx > max_allowed_idx:
            return TIER_ORDER[max_allowed_idx]
        return tier

    @staticmethod
    def apply_decay(
        score: float,
        days_inactive: int,
        decay_start: int = DEFAULT_DECAY_START_DAYS,
        half_life: int = DEFAULT_DECAY_HALF_LIFE,
    ) -> float:
        """Apply exponential decay for inactive users.

        Args:
            score: Current trust score.
            days_inactive: Days since last interaction.
            decay_start: Days before decay begins (grace period).
            half_life: Half-life in days of inactivity.

        Returns:
            Decayed score, floored at 50% of original.
        """
        if days_inactive <= decay_start:
            return score

        active_decay_days = days_inactive - decay_start
        lam = math.log(2) / max(1, half_life)
        decay_factor = math.exp(-lam * active_decay_days)

        # Floor at DECAY_FLOOR of original (residual trust for old friends)
        decay_factor = max(DECAY_FLOOR, decay_factor)

        return round(score * decay_factor, 3)

    @staticmethod
    def apply_violation(score: float, severity: float = 0.15) -> float:
        """Immediate trust drop from a violation event.

        Severity scale:
        - 0.05: mild (slightly rude, minor boundary push)
        - 0.15: moderate (hostile behavior, attempted manipulation)
        - 0.30: severe (harassment, doxxing attempt, explicit abuse)
        - 0.50: critical (direct attack on Ene/Dad, safety threat)
        """
        severity = max(0.0, min(1.0, severity))
        return round(max(0.0, score - severity), 3)

    @staticmethod
    def is_dad(platform_id: str) -> bool:
        """Check if a platform ID belongs to Dad."""
        return platform_id in DAD_IDS

    @staticmethod
    def tier_index(tier: str) -> int:
        """Get numeric index for a tier (0=stranger, 4=inner_circle)."""
        try:
            return TIER_ORDER.index(tier)
        except ValueError:
            return 0

    @staticmethod
    def compute_days_known(first_interaction: str) -> int:
        """Compute days since first interaction from an ISO timestamp."""
        if not first_interaction:
            return 0
        try:
            first_dt = datetime.fromisoformat(
                first_interaction.replace("Z", "+00:00")
            )
            if first_dt.tzinfo is None:
                first_dt = first_dt.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            return max(0, (now - first_dt).days)
        except (ValueError, AttributeError):
            return 0
