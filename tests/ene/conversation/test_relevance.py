"""Tests for conversation relevance classification (math-based, no LLM).

Tests the Naive Bayes relevance classifier, ChannelState tracker, and
conversation state detection from signals.py.
"""

import time
import math
import pytest

from nanobot.ene.conversation.signals import (
    relevance_features,
    relevance_score,
    classify_relevance,
    classify_with_state,
    ChannelState,
    RESPOND_THRESHOLD,
    CONTEXT_THRESHOLD,
    _sigmoid,
    _PRIOR_LOG_ODDS,
    _RELEVANCE_WEIGHTS,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def _score(**kwargs) -> float:
    """Shorthand: extract features, compute score."""
    features = relevance_features("test", "user:1", **kwargs)
    return relevance_score(features)


# ═══════════════════════════════════════════════════════════════════════
# Relevance Features
# ═══════════════════════════════════════════════════════════════════════


class TestRelevanceFeatures:
    """Test individual feature extraction."""

    def test_mention_feature(self):
        f = relevance_features("hello", "u1", is_at_mention=True)
        assert f["mention"] == 1.0

    def test_mention_absent(self):
        f = relevance_features("hello", "u1", is_at_mention=False)
        assert f["mention"] == 0.0

    def test_reply_feature(self):
        f = relevance_features("hello", "u1", is_reply_to_ene=True)
        assert f["reply"] == 1.0

    def test_reply_absent(self):
        f = relevance_features("hello", "u1")
        assert f["reply"] == 0.0

    def test_name_detection_word_boundary(self):
        f = relevance_features("hey ene how are you", "u1")
        assert f["name"] == 1.0

    def test_name_detection_case_insensitive(self):
        f = relevance_features("ENE wake up", "u1")
        assert f["name"] == 1.0

    def test_name_no_false_positive_substring(self):
        """'ene' inside 'generic' or 'scene' should NOT trigger."""
        f = relevance_features("this is a generic scene", "u1")
        assert f["name"] == 0.0

    def test_name_no_false_positive_energy(self):
        f = relevance_features("I have so much energy", "u1")
        assert f["name"] == 0.0

    def test_recency_just_spoke(self):
        f = relevance_features("hi", "u1", ene_last_spoke_seconds_ago=0.0)
        assert f["recency"] == pytest.approx(1.0, abs=0.01)

    def test_recency_2_minutes(self):
        f = relevance_features("hi", "u1", ene_last_spoke_seconds_ago=120.0)
        assert f["recency"] == pytest.approx(0.5, abs=0.01)

    def test_recency_10_minutes(self):
        f = relevance_features("hi", "u1", ene_last_spoke_seconds_ago=600.0)
        assert f["recency"] < 0.05  # nearly zero

    def test_recency_never_spoke(self):
        f = relevance_features("hi", "u1", ene_last_spoke_seconds_ago=float("inf"))
        assert f["recency"] == 0.0

    def test_question_mark(self):
        f = relevance_features("what time is it?", "u1")
        assert f["question"] == 1.0

    def test_question_word(self):
        f = relevance_features("how does this work", "u1")
        assert f["question"] == 0.5

    def test_not_question(self):
        f = relevance_features("nice job", "u1")
        assert f["question"] == 0.0

    def test_adjacency_ene_just_spoke(self):
        f = relevance_features(
            "thanks", "u1",
            ene_was_last_speaker=True,
            ene_last_spoke_seconds_ago=5.0,
        )
        assert f["adjacency"] > 0.5

    def test_adjacency_ene_not_last(self):
        f = relevance_features(
            "thanks", "u1",
            ene_was_last_speaker=False,
            ene_last_spoke_seconds_ago=5.0,
        )
        assert f["adjacency"] == 0.0

    def test_adjacency_ene_spoke_long_ago(self):
        f = relevance_features(
            "thanks", "u1",
            ene_was_last_speaker=True,
            ene_last_spoke_seconds_ago=120.0,
        )
        assert f["adjacency"] == 0.0

    def test_ene_thread_feature(self):
        f = relevance_features("hello", "u1", is_in_ene_thread=True)
        assert f["ene_thread"] == 1.0

    def test_author_hist_feature(self):
        f = relevance_features("hi", "u1", author_ene_interaction_ratio=0.8)
        assert f["author_hist"] == 0.8

    def test_all_features_present(self):
        """All 8 features should always be present."""
        f = relevance_features("hello", "u1")
        expected_keys = {"mention", "reply", "name", "recency",
                         "author_hist", "question", "ene_thread", "adjacency"}
        assert set(f.keys()) == expected_keys

    def test_all_zero_baseline(self):
        """No signals → all features near zero."""
        f = relevance_features("random chat", "u1")
        for key in f:
            assert f[key] == pytest.approx(0.0, abs=0.01), f"{key} should be ~0"


# ═══════════════════════════════════════════════════════════════════════
# Relevance Score
# ═══════════════════════════════════════════════════════════════════════


class TestRelevanceScore:
    """Test the Naive Bayes log-odds scoring."""

    def test_sigmoid_basic(self):
        assert _sigmoid(0.0) == pytest.approx(0.5)
        assert _sigmoid(100.0) == pytest.approx(1.0, abs=0.001)
        assert _sigmoid(-100.0) == pytest.approx(0.0, abs=0.001)

    def test_prior_alone(self):
        """No features → score should be near the prior (~0.15)."""
        features = {k: 0.0 for k in _RELEVANCE_WEIGHTS}
        score = relevance_score(features)
        assert score == pytest.approx(0.15, abs=0.02)

    def test_mention_alone_above_respond(self):
        """@mention alone should push above RESPOND threshold."""
        features = {k: 0.0 for k in _RELEVANCE_WEIGHTS}
        features["mention"] = 1.0
        score = relevance_score(features)
        assert score >= RESPOND_THRESHOLD

    def test_reply_alone_above_respond(self):
        features = {k: 0.0 for k in _RELEVANCE_WEIGHTS}
        features["reply"] = 1.0
        score = relevance_score(features)
        assert score >= RESPOND_THRESHOLD

    def test_name_alone_above_respond(self):
        features = {k: 0.0 for k in _RELEVANCE_WEIGHTS}
        features["name"] = 1.0
        score = relevance_score(features)
        assert score >= RESPOND_THRESHOLD

    def test_weak_signals_below_respond(self):
        """Only weak signals (question + recency) should stay below RESPOND."""
        features = {k: 0.0 for k in _RELEVANCE_WEIGHTS}
        features["question"] = 1.0
        features["recency"] = 0.5
        score = relevance_score(features)
        assert score < RESPOND_THRESHOLD

    def test_monotonicity(self):
        """Adding features should never decrease the score."""
        f1 = {k: 0.0 for k in _RELEVANCE_WEIGHTS}
        f2 = dict(f1)
        f2["recency"] = 0.5
        f3 = dict(f2)
        f3["author_hist"] = 0.3

        s1 = relevance_score(f1)
        s2 = relevance_score(f2)
        s3 = relevance_score(f3)
        assert s1 <= s2 <= s3

    def test_all_features_max(self):
        """All features at max → score near 1.0."""
        features = {k: 1.0 for k in _RELEVANCE_WEIGHTS}
        score = relevance_score(features)
        assert score > 0.99


# ═══════════════════════════════════════════════════════════════════════
# Classify Relevance (end-to-end)
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyRelevance:
    """Test the full classify_relevance() function."""

    def test_at_mention_respond(self):
        cls, score, _ = classify_relevance("hello", "u1", is_at_mention=True)
        assert cls == "respond"
        assert score >= RESPOND_THRESHOLD

    def test_reply_to_ene_respond(self):
        cls, score, _ = classify_relevance("thanks", "u1", is_reply_to_ene=True)
        assert cls == "respond"

    def test_name_mention_respond(self):
        cls, score, _ = classify_relevance("hey ene", "u1")
        assert cls == "respond"

    def test_no_signals_drop(self):
        cls, score, _ = classify_relevance("anyone want to play valorant", "u1")
        assert cls == "drop"
        assert score < CONTEXT_THRESHOLD

    def test_generic_not_ene(self):
        """Word-boundary: 'generic' should not trigger name detection."""
        cls, _, features = classify_relevance("this is a generic message", "u1")
        assert features["name"] == 0.0
        assert cls == "drop"

    def test_borderline_context(self):
        """Some weak signals → context (not respond, not drop)."""
        cls, score, _ = classify_relevance(
            "what do you think?", "u1",
            ene_last_spoke_seconds_ago=30.0,
            ene_was_last_speaker=True,
        )
        # Adjacency + recency + question should land in context or respond zone
        assert score >= CONTEXT_THRESHOLD

    def test_returns_features(self):
        """Features dict should be returned for debugging."""
        cls, score, features = classify_relevance("test", "u1")
        assert isinstance(features, dict)
        assert "mention" in features
        assert "reply" in features


# ═══════════════════════════════════════════════════════════════════════
# ChannelState
# ═══════════════════════════════════════════════════════════════════════


class TestChannelState:
    """Test per-channel state tracking."""

    def test_initial_state(self):
        state = ChannelState(channel_key="ch1")
        assert state.ene_last_spoke == 0.0
        assert state.ene_was_last_speaker is False

    def test_ene_last_spoke_seconds_ago_never(self):
        state = ChannelState(channel_key="ch1")
        assert state.ene_last_spoke_seconds_ago() == float("inf")

    def test_update_ene_message(self):
        state = ChannelState(channel_key="ch1")
        now = time.time()
        state.update("ene", now, is_ene=True)
        assert state.ene_last_spoke == now
        assert state.ene_was_last_speaker is True

    def test_update_user_clears_last_speaker(self):
        state = ChannelState(channel_key="ch1")
        now = time.time()
        state.update("ene", now, is_ene=True)
        state.update("user1", now + 1)
        assert state.ene_was_last_speaker is False

    def test_ene_last_spoke_seconds_ago_timed(self):
        state = ChannelState(channel_key="ch1")
        now = time.time()
        state.update("ene", now - 60.0, is_ene=True)
        elapsed = state.ene_last_spoke_seconds_ago(now)
        assert elapsed == pytest.approx(60.0, abs=1.0)

    def test_author_ene_ratio_no_history(self):
        state = ChannelState(channel_key="ch1")
        assert state.author_ene_ratio("user1") == 0.0

    def test_author_ene_ratio_tracks(self):
        state = ChannelState(channel_key="ch1")
        now = time.time()
        # User sends 10 messages, 3 interact with Ene
        for i in range(10):
            interacted = i < 3
            state.update("user1", now + i, interacted_with_ene=interacted)
        ratio = state.author_ene_ratio("user1")
        assert ratio == pytest.approx(0.3, abs=0.05)

    def test_estimate_rate_no_messages(self):
        state = ChannelState(channel_key="ch1")
        assert state.estimate_rate() == 0.0

    def test_estimate_rate_fast_chat(self):
        """10 messages in 60 seconds → ~10 msg/min."""
        state = ChannelState(channel_key="ch1")
        now = time.time()
        for i in range(10):
            state.update(f"u{i % 3}", now - 60 + i * 6)
        rate = state.estimate_rate(now)
        assert rate > 5.0  # should be close to 10/min

    def test_estimate_rate_slow_chat(self):
        """5 messages over 10 minutes → ~0.5 msg/min."""
        state = ChannelState(channel_key="ch1")
        now = time.time()
        for i in range(5):
            state.update(f"u{i}", now - 600 + i * 120)
        rate = state.estimate_rate(now)
        assert rate < 2.0

    def test_conversation_state_active(self):
        """Fast recent messages → active."""
        state = ChannelState(channel_key="ch1")
        now = time.time()
        for i in range(10):
            state.update(f"u{i % 3}", now - 30 + i * 3)
        assert state.conversation_state(now) == "active"

    def test_conversation_state_dead(self):
        """No messages for 15 minutes → dead."""
        state = ChannelState(channel_key="ch1")
        now = time.time()
        for i in range(5):
            state.update(f"u{i}", now - 1200 + i * 10)
        assert state.conversation_state(now) == "dead"

    def test_conversation_state_empty(self):
        """No messages at all → dead."""
        state = ChannelState(channel_key="ch1")
        now = time.time()
        assert state.conversation_state(now) in ("dead", "winding_down")

    def test_speaker_order(self):
        """Recent speakers tracked in order."""
        state = ChannelState(channel_key="ch1")
        now = time.time()
        state.update("alice", now)
        state.update("bob", now + 1)
        state.update("charlie", now + 2)
        speakers = list(state._recent_speakers)
        assert speakers[0] == "charlie"
        assert speakers[1] == "bob"


# ═══════════════════════════════════════════════════════════════════════
# Classify With State (integration)
# ═══════════════════════════════════════════════════════════════════════


class TestClassifyWithState:
    """Test the convenience function that combines ChannelState + classifier."""

    def test_basic_mention_with_state(self):
        state = ChannelState(channel_key="ch1")
        cls, score, features = classify_with_state(
            "hello", "u1", state, is_at_mention=True,
        )
        assert cls == "respond"

    def test_adjacency_pair(self):
        """Ene just spoke 5s ago + user responds → high relevance."""
        state = ChannelState(channel_key="ch1")
        now = time.time()
        state.update("ene", now - 5, is_ene=True)

        cls, score, features = classify_with_state(
            "thanks for that!", "u1", state, now=now,
        )
        assert features["adjacency"] > 0.5
        assert features["recency"] > 0.9

    def test_stale_channel_with_name(self):
        """Dead channel but user says 'ene' → still respond."""
        state = ChannelState(channel_key="ch1")
        now = time.time()
        # Ene spoke 30 min ago
        state.update("ene", now - 1800, is_ene=True)

        cls, score, _ = classify_with_state(
            "hey ene you there?", "u1", state, now=now,
        )
        assert cls == "respond"

    def test_no_signals_with_state(self):
        """No signals even with channel state → drop."""
        state = ChannelState(channel_key="ch1")
        now = time.time()
        state.update("bob", now - 10)
        state.update("alice", now - 5)

        cls, score, _ = classify_with_state(
            "gg wp", "u1", state, now=now,
        )
        assert cls == "drop"

    def test_frequent_interactor_boost(self):
        """User who frequently talks to Ene gets slight boost."""
        state = ChannelState(channel_key="ch1")
        now = time.time()
        # User has 80% interaction ratio with Ene
        for i in range(10):
            state.update("u1", now - 100 + i * 10, interacted_with_ene=(i < 8))

        _, score_with_hist, f_hist = classify_with_state(
            "interesting", "u1", state, now=now,
        )
        assert f_hist["author_hist"] > 0.5

        # Compare with a user who never interacts with Ene
        _, score_no_hist, f_no = classify_with_state(
            "interesting", "u2", state, now=now,
        )
        assert score_with_hist > score_no_hist
