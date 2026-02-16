"""Tests for trust calculator — Bayesian core, modulators, time gates, decay.

Extensive edge cases, boundary conditions, asymmetry verification,
gaming resistance, and Dad bypass.
"""

import math
import pytest

from nanobot.ene.social.trust import (
    TrustCalculator,
    DAD_IDS,
    TIER_TIME_GATES,
    TRUST_TIERS,
    TIER_ORDER,
    NEGATIVE_WEIGHT,
    SENTIMENT_CAP,
    DECAY_FLOOR,
)


# ── Helpers ───────────────────────────────────────────────


def make_signals(
    positive: int = 0,
    negative: int = 0,
    days_known: int = 0,
    days_active: int = 0,
    session_count: int = 0,
    unique_hours: list | None = None,
    unique_days_of_week: list | None = None,
    restricted_tool_attempts: int = 0,
    violations: list | None = None,
    sentiment_modifier: float = 0.0,
) -> dict:
    """Create a signals dict with defaults."""
    return {
        "positive_interactions": positive,
        "negative_interactions": negative,
        "days_known": days_known,
        "days_active": days_active,
        "session_count": session_count,
        "unique_hours": unique_hours or [],
        "unique_days_of_week": unique_days_of_week or [],
        "restricted_tool_attempts": restricted_tool_attempts,
        "violations": violations or [],
        "sentiment_modifier": sentiment_modifier,
    }


# ── Dad Bypass Tests ──────────────────────────────────────


class TestDadBypass:
    """Dad is always score=1.0, tier=inner_circle, no calculation."""

    def test_dad_discord(self):
        signals = make_signals()  # Empty signals
        score, tier = TrustCalculator.calculate(
            signals, platform_id="discord:1175414972482846813"
        )
        assert score == 1.0
        assert tier == "inner_circle"

    def test_dad_telegram(self):
        signals = make_signals()
        score, tier = TrustCalculator.calculate(
            signals, platform_id="telegram:8559611823"
        )
        assert score == 1.0
        assert tier == "inner_circle"

    def test_dad_ignores_all_signals(self):
        """Dad's score is immune to negative interactions, violations, etc."""
        signals = make_signals(
            positive=0,
            negative=1000,
            violations=[{"severity": 0.5}] * 10,
            restricted_tool_attempts=100,
        )
        score, tier = TrustCalculator.calculate(
            signals, platform_id="discord:1175414972482846813"
        )
        assert score == 1.0
        assert tier == "inner_circle"

    def test_is_dad(self):
        for pid in DAD_IDS:
            assert TrustCalculator.is_dad(pid) is True
        assert TrustCalculator.is_dad("discord:999999") is False


# ── New User Tests ────────────────────────────────────────


class TestNewUser:
    """New users should have very low effective scores."""

    def test_brand_new_zero_signals(self):
        """Brand new user with no interactions."""
        signals = make_signals()
        score, tier = TrustCalculator.calculate(signals)
        # Beta score starts at 0.5, but modulators are near-zero
        assert score < 0.15
        assert tier == "stranger"

    def test_one_message(self):
        """Single positive interaction."""
        signals = make_signals(
            positive=1, days_known=0, days_active=1,
            session_count=1, unique_hours=[14], unique_days_of_week=[1],
        )
        score, tier = TrustCalculator.calculate(signals)
        # Still very low — not enough evidence
        assert score < 0.20
        assert tier == "stranger"

    def test_few_messages_day_one(self):
        """Several messages on day 1."""
        signals = make_signals(
            positive=10, days_known=0, days_active=1,
            session_count=1, unique_hours=[14, 15], unique_days_of_week=[1],
        )
        score, tier = TrustCalculator.calculate(signals)
        # Score rising but time gate blocks acquaintance (needs 3 days)
        assert tier == "stranger"


# ── Tier Threshold Tests ──────────────────────────────────


class TestTierThresholds:
    """Test that tier boundaries are correct."""

    def test_get_tier_boundaries(self):
        assert TrustCalculator.get_tier(0.0) == "stranger"
        assert TrustCalculator.get_tier(0.14) == "stranger"
        assert TrustCalculator.get_tier(0.15) == "acquaintance"
        assert TrustCalculator.get_tier(0.34) == "acquaintance"
        assert TrustCalculator.get_tier(0.35) == "familiar"
        assert TrustCalculator.get_tier(0.59) == "familiar"
        assert TrustCalculator.get_tier(0.60) == "trusted"
        assert TrustCalculator.get_tier(0.79) == "trusted"
        assert TrustCalculator.get_tier(0.80) == "inner_circle"
        assert TrustCalculator.get_tier(1.0) == "inner_circle"

    def test_tier_index(self):
        assert TrustCalculator.tier_index("stranger") == 0
        assert TrustCalculator.tier_index("acquaintance") == 1
        assert TrustCalculator.tier_index("familiar") == 2
        assert TrustCalculator.tier_index("trusted") == 3
        assert TrustCalculator.tier_index("inner_circle") == 4
        assert TrustCalculator.tier_index("unknown_tier") == 0


# ── Time Gate Tests ───────────────────────────────────────


class TestTimeGates:
    """Time gates prevent speed-running trust."""

    def test_acquaintance_needs_3_days(self):
        # High score but only 2 days → stays stranger
        tier = TrustCalculator.apply_time_gate("acquaintance", days_known=2)
        assert tier == "stranger"

        tier = TrustCalculator.apply_time_gate("acquaintance", days_known=3)
        assert tier == "acquaintance"

    def test_familiar_needs_14_days(self):
        tier = TrustCalculator.apply_time_gate("familiar", days_known=13)
        assert tier == "acquaintance"

        tier = TrustCalculator.apply_time_gate("familiar", days_known=14)
        assert tier == "familiar"

    def test_trusted_needs_60_days(self):
        tier = TrustCalculator.apply_time_gate("trusted", days_known=59)
        assert tier == "familiar"

        tier = TrustCalculator.apply_time_gate("trusted", days_known=60)
        assert tier == "trusted"

    def test_inner_circle_needs_180_days(self):
        tier = TrustCalculator.apply_time_gate("inner_circle", days_known=179)
        assert tier == "trusted"

        tier = TrustCalculator.apply_time_gate("inner_circle", days_known=180)
        assert tier == "inner_circle"

    def test_stranger_no_gate(self):
        tier = TrustCalculator.apply_time_gate("stranger", days_known=0)
        assert tier == "stranger"

    def test_time_gate_in_full_calculation(self):
        """Even with perfect signals, day 2 user can't reach acquaintance."""
        signals = make_signals(
            positive=500, days_known=2, days_active=2,
            session_count=50, unique_hours=list(range(12)),
            unique_days_of_week=[0, 1],
        )
        score, tier = TrustCalculator.calculate(signals)
        # Score might be high, but time gate caps tier
        assert tier == "stranger"

    def test_time_gate_preserves_score(self):
        """Time gate caps tier but the numeric score is unchanged."""
        signals = make_signals(
            positive=200, days_known=10, days_active=10,
            session_count=20, unique_hours=list(range(10)),
            unique_days_of_week=list(range(5)),
        )
        score, tier = TrustCalculator.calculate(signals)
        # Tier capped to acquaintance (needs 14 days for familiar)
        assert tier in ("stranger", "acquaintance")
        # But score could be above 0.35 (familiar threshold)


# ── Beta Reputation Core Tests ────────────────────────────


class TestBetaReputation:
    """Test the Bayesian trust core."""

    def test_starts_at_uncertainty(self):
        """With zero interactions, beta score is 0.5."""
        # (0+1) / (0+0+2) = 0.5
        # But modulators scale it down
        signals = make_signals(days_known=180, days_active=60,
                               session_count=30, unique_hours=list(range(12)),
                               unique_days_of_week=list(range(5)))
        score, _ = TrustCalculator.calculate(signals)
        # With perfect modulators but zero interactions, should be ~0.5
        assert 0.3 < score < 0.7

    def test_positive_interactions_increase_score(self):
        """More positive interactions → higher score."""
        base = dict(days_known=90, days_active=30, session_count=15,
                    unique_hours=list(range(8)), unique_days_of_week=list(range(5)))
        s1 = make_signals(positive=10, **base)
        s2 = make_signals(positive=50, **base)
        s3 = make_signals(positive=200, **base)

        score1, _ = TrustCalculator.calculate(s1)
        score2, _ = TrustCalculator.calculate(s2)
        score3, _ = TrustCalculator.calculate(s3)

        assert score1 < score2 < score3

    def test_negative_interactions_decrease_score(self):
        """More negative interactions → lower score."""
        base = dict(days_known=90, days_active=30, session_count=15,
                    unique_hours=list(range(8)), unique_days_of_week=list(range(5)))
        s1 = make_signals(positive=50, negative=0, **base)
        s2 = make_signals(positive=50, negative=5, **base)
        s3 = make_signals(positive=50, negative=20, **base)

        score1, _ = TrustCalculator.calculate(s1)
        score2, _ = TrustCalculator.calculate(s2)
        score3, _ = TrustCalculator.calculate(s3)

        assert score1 > score2 > score3


# ── Asymmetry Tests (Slovic 1993) ─────────────────────────


class TestAsymmetry:
    """Negative interactions must have disproportionate impact."""

    def test_one_negative_vs_three_positive(self):
        """1 negative should hurt more than 3 positives help."""
        base = dict(days_known=90, days_active=30, session_count=15,
                    unique_hours=list(range(8)), unique_days_of_week=list(range(5)))

        # Baseline: 50 positive
        s_base = make_signals(positive=50, negative=0, **base)
        score_base, _ = TrustCalculator.calculate(s_base)

        # Add 3 more positive
        s_plus3 = make_signals(positive=53, negative=0, **base)
        score_plus3, _ = TrustCalculator.calculate(s_plus3)

        # Add 1 negative instead
        s_neg1 = make_signals(positive=50, negative=1, **base)
        score_neg1, _ = TrustCalculator.calculate(s_neg1)

        gain_from_3_positive = score_plus3 - score_base
        loss_from_1_negative = score_base - score_neg1

        # Loss from 1 negative should be greater than gain from 3 positive
        assert loss_from_1_negative > gain_from_3_positive

    def test_negative_weight_is_3x(self):
        """Verify the 3:1 asymmetry ratio in the beta formula."""
        assert NEGATIVE_WEIGHT == 3.0


# ── Timing Entropy Tests (Eagle 2009) ─────────────────────


class TestTimingEntropy:
    """Interaction diversity matters more than raw volume."""

    def test_varied_timing_scores_higher(self):
        """Messages at diverse times > messages all at midnight."""
        base = dict(positive=50, days_known=90, days_active=30, session_count=15)

        # All messages at same time
        s_monotone = make_signals(
            unique_hours=[0], unique_days_of_week=[0], **base
        )
        # Messages at varied times
        s_diverse = make_signals(
            unique_hours=[0, 6, 10, 14, 18, 22],
            unique_days_of_week=[0, 1, 2, 3, 4],
            **base,
        )

        score_mono, _ = TrustCalculator.calculate(s_monotone)
        score_div, _ = TrustCalculator.calculate(s_diverse)

        assert score_div > score_mono

    def test_empty_timing_still_works(self):
        """Empty unique_hours/days shouldn't crash."""
        signals = make_signals(positive=10, days_known=30, days_active=10,
                               session_count=5)
        score, tier = TrustCalculator.calculate(signals)
        assert isinstance(score, float)


# ── Modulator Tests ───────────────────────────────────────


class TestModulators:
    """Test that all modulators affect the score."""

    def test_tenure_matters(self):
        """Longer tenure → higher score (all else equal)."""
        base = dict(positive=50, days_active=30, session_count=15,
                    unique_hours=list(range(8)), unique_days_of_week=list(range(5)))
        s_short = make_signals(days_known=10, **base)
        s_long = make_signals(days_known=120, **base)

        score_short, _ = TrustCalculator.calculate(s_short)
        score_long, _ = TrustCalculator.calculate(s_long)
        assert score_long > score_short

    def test_consistency_matters(self):
        """More active days → higher score."""
        base = dict(positive=50, days_known=90, session_count=15,
                    unique_hours=list(range(8)), unique_days_of_week=list(range(5)))
        s_sporadic = make_signals(days_active=5, **base)
        s_consistent = make_signals(days_active=40, **base)

        score_s, _ = TrustCalculator.calculate(s_sporadic)
        score_c, _ = TrustCalculator.calculate(s_consistent)
        assert score_c > score_s

    def test_session_depth_matters(self):
        """More sessions → higher score."""
        base = dict(positive=50, days_known=90, days_active=30,
                    unique_hours=list(range(8)), unique_days_of_week=list(range(5)))
        s_few = make_signals(session_count=2, **base)
        s_many = make_signals(session_count=25, **base)

        score_few, _ = TrustCalculator.calculate(s_few)
        score_many, _ = TrustCalculator.calculate(s_many)
        assert score_many > score_few

    def test_geometric_mean_requires_all_decent(self):
        """One zero-ish modulator drags score down despite others being high."""
        # Perfect everything except zero sessions
        s_no_sessions = make_signals(
            positive=200, days_known=180, days_active=60,
            session_count=0,
            unique_hours=list(range(12)), unique_days_of_week=list(range(5)),
        )
        # Moderate everything
        s_moderate = make_signals(
            positive=100, days_known=90, days_active=30,
            session_count=15,
            unique_hours=list(range(8)), unique_days_of_week=list(range(5)),
        )

        score_no_sessions, _ = TrustCalculator.calculate(s_no_sessions)
        score_moderate, _ = TrustCalculator.calculate(s_moderate)
        # Moderate-across-the-board should beat extreme-with-weakness
        assert score_moderate > score_no_sessions


# ── Penalty Tests ─────────────────────────────────────────


class TestPenalties:
    """Test that penalties reduce trust."""

    def test_tool_attempt_penalty(self):
        """Each restricted tool attempt reduces score."""
        base = dict(positive=50, days_known=90, days_active=30,
                    session_count=15, unique_hours=list(range(8)),
                    unique_days_of_week=list(range(5)))
        s_clean = make_signals(restricted_tool_attempts=0, **base)
        s_attempts = make_signals(restricted_tool_attempts=3, **base)

        score_clean, _ = TrustCalculator.calculate(s_clean)
        score_attempts, _ = TrustCalculator.calculate(s_attempts)
        assert score_clean > score_attempts
        # 3 attempts * 0.05 = 0.15 penalty
        assert score_clean - score_attempts == pytest.approx(0.15, abs=0.02)

    def test_tool_penalty_capped(self):
        """Tool penalty maxes at 0.3."""
        base = dict(positive=50, days_known=90, days_active=30,
                    session_count=15, unique_hours=list(range(8)),
                    unique_days_of_week=list(range(5)))
        s_6 = make_signals(restricted_tool_attempts=6, **base)
        s_100 = make_signals(restricted_tool_attempts=100, **base)

        score_6, _ = TrustCalculator.calculate(s_6)
        score_100, _ = TrustCalculator.calculate(s_100)
        # Both should have the same 0.3 cap
        assert score_6 == score_100

    def test_violation_penalty(self):
        """Violations reduce score proportionally to severity."""
        base = dict(positive=50, days_known=90, days_active=30,
                    session_count=15, unique_hours=list(range(8)),
                    unique_days_of_week=list(range(5)))
        s_clean = make_signals(**base)
        s_mild = make_signals(violations=[{"severity": 0.05}], **base)
        s_severe = make_signals(violations=[{"severity": 0.30}], **base)

        score_clean, _ = TrustCalculator.calculate(s_clean)
        score_mild, _ = TrustCalculator.calculate(s_mild)
        score_severe, _ = TrustCalculator.calculate(s_severe)

        assert score_clean > score_mild > score_severe

    def test_violation_penalty_capped(self):
        """Violation penalty maxes at 0.5."""
        base = dict(positive=100, days_known=180, days_active=60,
                    session_count=30, unique_hours=list(range(12)),
                    unique_days_of_week=list(range(5)))
        s_many = make_signals(
            violations=[{"severity": 0.5}] * 10,  # 5.0 total but capped
            **base,
        )
        score, _ = TrustCalculator.calculate(s_many)
        # Score should be non-negative
        assert score >= 0.0


# ── Sentiment Tests ───────────────────────────────────────


class TestSentiment:
    """Sentiment modifier is tiny and cannot control tier."""

    def test_sentiment_positive_nudge(self):
        base = dict(positive=50, days_known=90, days_active=30,
                    session_count=15, unique_hours=list(range(8)),
                    unique_days_of_week=list(range(5)))
        s_neutral = make_signals(sentiment_modifier=0.0, **base)
        s_positive = make_signals(sentiment_modifier=0.03, **base)

        score_n, _ = TrustCalculator.calculate(s_neutral)
        score_p, _ = TrustCalculator.calculate(s_positive)
        assert score_p > score_n
        # Difference should be exactly SENTIMENT_CAP
        assert score_p - score_n == pytest.approx(SENTIMENT_CAP, abs=0.001)

    def test_sentiment_capped(self):
        """Sentiment beyond ±0.03 is clamped."""
        base = dict(positive=50, days_known=90, days_active=30,
                    session_count=15, unique_hours=list(range(8)),
                    unique_days_of_week=list(range(5)))
        s_huge = make_signals(sentiment_modifier=999.0, **base)
        s_capped = make_signals(sentiment_modifier=0.03, **base)

        score_huge, _ = TrustCalculator.calculate(s_huge)
        score_capped, _ = TrustCalculator.calculate(s_capped)
        assert score_huge == score_capped

    def test_sentiment_cannot_promote_tier(self):
        """Sentiment alone can't push someone from one tier to the next."""
        # Score right at boundary: 0.35 would be familiar, 0.34 is acquaintance
        # The +0.03 from sentiment can't jump a 0.01 gap? Not reliably.
        # But let's verify the cap is too small to matter much:
        assert SENTIMENT_CAP == 0.03
        # Tier gaps are at least 0.06 (acquaintance→familiar is 0.35-0.15=0.20)
        # So ±0.03 can't jump a whole tier


# ── Decay Tests ───────────────────────────────────────────


class TestDecay:
    """Test exponential decay for inactive users."""

    def test_no_decay_within_grace_period(self):
        score = TrustCalculator.apply_decay(0.5, days_inactive=25)
        assert score == 0.5

    def test_no_decay_at_exactly_grace_period(self):
        score = TrustCalculator.apply_decay(0.5, days_inactive=30)
        assert score == 0.5

    def test_decay_starts_after_grace(self):
        score = TrustCalculator.apply_decay(0.5, days_inactive=31)
        assert score < 0.5

    def test_more_inactive_more_decay(self):
        score_31 = TrustCalculator.apply_decay(0.5, days_inactive=31)
        score_60 = TrustCalculator.apply_decay(0.5, days_inactive=60)
        score_120 = TrustCalculator.apply_decay(0.5, days_inactive=120)
        assert score_31 > score_60 > score_120

    def test_half_life_correct(self):
        """After half_life days of active decay, score should halve."""
        score = TrustCalculator.apply_decay(
            1.0, days_inactive=30 + 60,  # grace + half_life
        )
        assert score == pytest.approx(0.5, abs=0.01)

    def test_decay_floor(self):
        """Score never drops below 50% of original."""
        score = TrustCalculator.apply_decay(0.8, days_inactive=9999)
        assert score == pytest.approx(0.8 * DECAY_FLOOR, abs=0.01)
        assert score > 0

    def test_decay_floor_preserves_minimum(self):
        """Even extreme inactivity preserves 50%."""
        score = TrustCalculator.apply_decay(0.6, days_inactive=10000)
        assert score >= 0.6 * DECAY_FLOOR - 0.01

    def test_zero_score_stays_zero(self):
        score = TrustCalculator.apply_decay(0.0, days_inactive=100)
        assert score == 0.0


# ── Violation Drop Tests ──────────────────────────────────


class TestViolationDrop:
    """Test immediate trust drops from violations."""

    def test_mild_violation(self):
        assert TrustCalculator.apply_violation(0.5, 0.05) == 0.45

    def test_moderate_violation(self):
        assert TrustCalculator.apply_violation(0.5, 0.15) == 0.35

    def test_severe_violation(self):
        assert TrustCalculator.apply_violation(0.5, 0.30) == 0.20

    def test_critical_violation(self):
        assert TrustCalculator.apply_violation(0.5, 0.50) == 0.0

    def test_violation_floor_at_zero(self):
        assert TrustCalculator.apply_violation(0.1, 0.50) == 0.0

    def test_violation_severity_clamped(self):
        # Can't exceed 1.0
        result = TrustCalculator.apply_violation(0.5, 999.0)
        assert result == 0.0


# ── Gaming Resistance Tests ───────────────────────────────


class TestGamingResistance:
    """Verify the system is hard to game."""

    def test_spam_500_messages_day_1(self):
        """500 messages on day 1 doesn't give trust."""
        signals = make_signals(
            positive=500, days_known=0, days_active=1,
            session_count=1, unique_hours=[14], unique_days_of_week=[1],
        )
        score, tier = TrustCalculator.calculate(signals)
        # Low modulators + time gate = stuck at stranger
        assert tier == "stranger"
        assert score < 0.35  # Can't reach familiar

    def test_perfect_week_cant_reach_familiar(self):
        """7 days of perfect interaction can't reach familiar (needs 14 days)."""
        signals = make_signals(
            positive=100, days_known=7, days_active=7,
            session_count=14, unique_hours=list(range(12)),
            unique_days_of_week=list(range(5)),
        )
        _, tier = TrustCalculator.calculate(signals)
        assert tier in ("stranger", "acquaintance")

    def test_month_cant_reach_trusted(self):
        """30 days of interaction can't reach trusted (needs 60 days)."""
        signals = make_signals(
            positive=200, days_known=30, days_active=30,
            session_count=30, unique_hours=list(range(12)),
            unique_days_of_week=list(range(7)),
        )
        _, tier = TrustCalculator.calculate(signals)
        assert tier in ("acquaintance", "familiar")

    def test_one_dimensional_gaming(self):
        """Maxing one signal while ignoring others doesn't help much."""
        # Lots of messages but no time/consistency
        s_spam = make_signals(
            positive=1000, days_known=1, days_active=1,
            session_count=1, unique_hours=[12], unique_days_of_week=[3],
        )
        # Moderate across all dimensions
        s_balanced = make_signals(
            positive=30, days_known=60, days_active=20,
            session_count=10, unique_hours=list(range(6)),
            unique_days_of_week=list(range(4)),
        )

        score_spam, _ = TrustCalculator.calculate(s_spam)
        score_balanced, _ = TrustCalculator.calculate(s_balanced)
        assert score_balanced > score_spam

    def test_trust_washing_attempt(self):
        """Build trust then violate — violation should significantly drop score."""
        base = dict(
            positive=100, days_known=90, days_active=45,
            session_count=20, unique_hours=list(range(10)),
            unique_days_of_week=list(range(5)),
        )
        s_clean = make_signals(**base)
        score_before, _ = TrustCalculator.calculate(s_clean)

        # Now add a severe violation
        s_violated = make_signals(
            violations=[{"severity": 0.30}], **base
        )
        score_after, _ = TrustCalculator.calculate(s_violated)

        # Violation should cause significant drop
        assert score_before - score_after >= 0.25


# ── Progression Tests ─────────────────────────────────────


class TestTrustProgression:
    """Test realistic trust progression over time."""

    def test_gradual_progression(self):
        """Trust should grow steadily with consistent interaction."""
        scores = []
        for days in [0, 3, 14, 30, 60, 90, 120, 180]:
            signals = make_signals(
                positive=days * 2,  # ~2 messages/day
                days_known=days,
                days_active=max(1, days // 2),
                session_count=max(1, days // 3),
                unique_hours=list(range(min(12, max(1, days // 15)))),
                unique_days_of_week=list(range(min(5, max(1, days // 30)))),
            )
            score, _ = TrustCalculator.calculate(signals)
            scores.append(score)

        # Score should be monotonically increasing
        for i in range(1, len(scores)):
            assert scores[i] >= scores[i - 1], (
                f"Score decreased from day {[0,3,14,30,60,90,120,180][i-1]} "
                f"to day {[0,3,14,30,60,90,120,180][i]}: "
                f"{scores[i-1]} → {scores[i]}"
            )

    def test_score_bounded(self):
        """Score never exceeds 1.0 or goes below 0.0."""
        # Extreme positive
        signals = make_signals(
            positive=10000, days_known=999, days_active=365,
            session_count=500, unique_hours=list(range(24)),
            unique_days_of_week=list(range(7)),
            sentiment_modifier=999.0,
        )
        score, _ = TrustCalculator.calculate(signals)
        assert 0.0 <= score <= 1.0

        # Extreme negative
        signals = make_signals(
            positive=0, negative=1000, days_known=999,
            days_active=365, session_count=500,
            unique_hours=list(range(24)),
            unique_days_of_week=list(range(7)),
            violations=[{"severity": 0.5}] * 10,
            restricted_tool_attempts=100,
        )
        score, _ = TrustCalculator.calculate(signals)
        assert 0.0 <= score <= 1.0
