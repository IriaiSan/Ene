"""Signal scoring functions for conversation analysis.

Two systems:
1. **Thread assignment** — which thread does this message belong to?
2. **Relevance classification** — should Ene respond, absorb, or ignore?

Pure math, no LLM calls, no embeddings. Under 1ms per message.

The relevance classifier uses a Naive Bayes-style log-odds combination
of weighted features (research-backed: Ouchi & Tsuboi 2016, Kummerfeld
et al. 2019). Each feature is [0, 1], combined via:

    P(for_ene) = sigmoid(prior_log_odds + W . F)

Decision boundaries:
    >= 0.7  → RESPOND (high confidence)
    [0.3, 0.7) → CONTEXT (absorb, don't respond)
    < 0.3  → DROP (ignore)
"""

from __future__ import annotations

import math
import re
import time as _time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.ene.observatory.module_metrics import ModuleMetrics
    from .models import Thread, ThreadMessage, PendingMessage

# Module-level metrics instance — set by set_metrics() during init.
# NullModuleMetrics is used when observatory is unavailable.
_metrics: "ModuleMetrics | None" = None


def set_metrics(metrics: "ModuleMetrics") -> None:
    """Attach a ModuleMetrics instance for classification observability.

    Called once during module initialization (from loop.py or module setup).
    """
    global _metrics
    _metrics = metrics

# ── Signal weights ───────────────────────────────────────────────────────
REPLY_CHAIN_WEIGHT = 1.0
MENTION_WEIGHT = 0.9
TEMPORAL_WEIGHT = 0.6
SPEAKER_WEIGHT = 0.4
LEXICAL_WEIGHT = 0.3

# ── Stopwords (English + Discord slang) ─────────────────────────────────
_STOPWORDS: frozenset[str] = frozenset({
    # English common
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "not", "and", "but", "or",
    "nor", "for", "yet", "so", "in", "on", "at", "to", "of", "by",
    "with", "from", "up", "out", "if", "then", "than", "too", "very",
    "just", "about", "also", "that", "this", "these", "those", "what",
    "which", "who", "how", "when", "where", "why", "its", "it", "i",
    "me", "my", "we", "our", "you", "your", "he", "she", "they",
    "them", "her", "his", "all", "each", "some", "any", "most", "more",
    "other", "into", "over", "only", "own", "same", "such", "no", "not",
    "don", "now", "here", "there", "back", "even", "well", "still",
    "get", "got", "know", "think", "say", "said", "make", "made",
    "let", "like", "come", "came", "take", "took", "see", "saw",
    "want", "tell", "told", "give", "gave", "way", "thing", "good",
    "new", "first", "last", "long", "great", "little", "right", "look",
    # Discord / chat slang
    "lol", "lmao", "lmfao", "rofl", "haha", "hehe", "xd", "xdd",
    "yeah", "yes", "yep", "yea", "yah", "nah", "nope", "ngl", "tbh",
    "imo", "imho", "idk", "idc", "bruh", "bro", "dude", "man", "guys",
    "like", "gonna", "wanna", "gotta", "kinda", "sorta", "tho",
    "ene", "lmk", "omg", "omfg", "smh", "fyi", "btw", "irl",
    "rn", "rip", "gg", "ez", "pog", "poggers", "based", "cap", "nocap",
    "sus", "vibe", "vibes", "lit", "fire", "bet", "aight", "ight",
    "msg", "damn", "dang", "huh", "hmm", "uhh", "umm", "mhm",
    "hey", "hello", "hi", "sup", "yo", "ayy",
})

# ── Keyword extraction ───────────────────────────────────────────────────
_WORD_PATTERN = re.compile(r"[a-zA-Z]{3,}")


def extract_keywords(text: str, max_keywords: int = 5) -> list[str]:
    """Extract content words from text.

    Returns lowercase words, stopwords filtered, frequency-ranked.
    """
    words = _WORD_PATTERN.findall(text.lower())
    filtered = [w for w in words if w not in _STOPWORDS]
    if not filtered:
        return []
    counts = Counter(filtered)
    return [word for word, _ in counts.most_common(max_keywords)]


# ── Signal scoring functions ─────────────────────────────────────────────


def score_reply_chain(msg: ThreadMessage, thread: Thread) -> float:
    """Score based on explicit Discord reply-to link.

    If the message replies to a message that's in this thread, it's
    almost certainly part of this thread. Strongest signal.
    """
    if msg.reply_to_msg_id and msg.reply_to_msg_id in thread.discord_msg_ids:
        return REPLY_CHAIN_WEIGHT
    return 0.0


def score_mention_affinity(
    msg: ThreadMessage,
    thread: Thread,
    name_resolver: dict[str, str] | None = None,
) -> float:
    """Score based on mentioning a thread participant by name.

    name_resolver maps platform_id -> display_name for lookup.
    """
    if not name_resolver:
        return 0.0

    content_lower = msg.content.lower()
    for participant_id in thread.participants:
        name = name_resolver.get(participant_id, "")
        if name and len(name) >= 3 and name.lower() in content_lower:
            return MENTION_WEIGHT
    return 0.0


def score_temporal(msg: ThreadMessage, thread: Thread) -> float:
    """Score based on time proximity to the thread's last message.

    Decay curve: closer in time = stronger signal.
    Max is 0.4 — temporal alone should never pass the assignment threshold.
    It must combine with at least one other signal (speaker, lexical, etc.).
    """
    recency = msg.timestamp - thread.updated_at
    if recency < 0:
        recency = 0  # Message arrived before thread updated (clock skew)
    if recency < 10:
        return 0.4
    elif recency < 30:
        return 0.3
    elif recency < 120:
        return 0.2
    elif recency < 300:
        return 0.1
    return 0.0


def score_speaker(msg: ThreadMessage, thread: Thread) -> float:
    """Score based on speaker already being a thread participant."""
    if msg.author_id in thread.participants:
        return SPEAKER_WEIGHT
    return 0.0


def score_lexical(msg: ThreadMessage, thread: Thread) -> float:
    """Score based on keyword overlap with thread's topic.

    Uses simple word intersection — no embeddings.
    """
    msg_words = set(extract_keywords(msg.content))
    thread_words = set(thread.topic_keywords)
    if not msg_words or not thread_words:
        return 0.0
    overlap = len(msg_words & thread_words)
    # 3+ overlapping keywords = max signal
    ratio = min(overlap / 3.0, 1.0)
    return LEXICAL_WEIGHT * ratio


def compute_thread_score(
    msg: ThreadMessage,
    thread: Thread,
    name_resolver: dict[str, str] | None = None,
) -> float:
    """Compute the total assignment score for a message against a thread.

    Returns the sum of all signal scores.
    """
    return (
        score_reply_chain(msg, thread)
        + score_mention_affinity(msg, thread, name_resolver)
        + score_temporal(msg, thread)
        + score_speaker(msg, thread)
        + score_lexical(msg, thread)
    )


# ── Pending message scoring ─────────────────────────────────────────────


def score_against_pending(
    msg: ThreadMessage,
    pending: PendingMessage,
    name_resolver: dict[str, str] | None = None,
) -> float:
    """Score a new message against a pending (single) message.

    Uses a subset of signals: reply chain, temporal, speaker, lexical.
    Pending messages don't have a full thread structure, so we
    simulate it with the single message's data.
    """
    score = 0.0
    pm = pending.message

    # Reply chain
    if msg.reply_to_msg_id and msg.reply_to_msg_id == pm.discord_msg_id:
        score += REPLY_CHAIN_WEIGHT

    # Temporal (max 0.4 — can't pass threshold alone)
    recency = msg.timestamp - pm.timestamp
    if recency < 0:
        recency = 0
    if recency < 10:
        score += 0.4
    elif recency < 30:
        score += 0.3
    elif recency < 120:
        score += 0.2
    elif recency < 300:
        score += 0.1

    # Speaker continuity (same person continuing)
    if msg.author_id == pm.author_id:
        score += SPEAKER_WEIGHT

    # Lexical
    msg_words = set(extract_keywords(msg.content))
    pending_words = set(extract_keywords(pm.content))
    if msg_words and pending_words:
        overlap = len(msg_words & pending_words)
        ratio = min(overlap / 3.0, 1.0)
        score += LEXICAL_WEIGHT * ratio

    # Mention affinity (does the new msg mention the pending msg's author?)
    if name_resolver:
        content_lower = msg.content.lower()
        name = name_resolver.get(pm.author_id, "")
        if name and len(name) >= 3 and name.lower() in content_lower:
            score += MENTION_WEIGHT

    return score


# ═══════════════════════════════════════════════════════════════════════
# PART 2: Relevance Classifier — "Is this message for Ene?"
# ═══════════════════════════════════════════════════════════════════════

# Word-boundary match for "ene" (avoids "generic", "scene", etc.)
_ENE_PATTERN = re.compile(r"\bene\b", re.IGNORECASE)

# Feature weights — tunable. Higher = stronger signal.
# Based on addressee detection research (Ouchi & Tsuboi 2016):
# explicit signals (@mention, reply, name) >> implicit (recency, history).
_RELEVANCE_WEIGHTS = {
    "mention":        6.0,   # @Ene or bot mention ID
    "reply":          5.5,   # Discord reply to Ene's message
    "name":           4.0,   # "ene" in message content (word boundary)
    "recency":        1.5,   # Ene spoke recently in this channel
    "author_hist":    1.0,   # This author frequently engages Ene
    "question":       0.5,   # Message is a question (slightly more likely directed)
    "ene_thread":     2.0,   # Message is in a thread where Ene is involved
    "adjacency":      1.5,   # Message follows Ene's message (adjacency pair)
}

# Prior: ~15% of messages in a group chat are directed at the bot.
_PRIOR_LOG_ODDS = math.log(0.15 / 0.85)  # ≈ -1.735

# Thresholds for classification
RESPOND_THRESHOLD = 0.7
CONTEXT_THRESHOLD = 0.3


def _sigmoid(x: float) -> float:
    """Numerically stable sigmoid."""
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    else:
        ex = math.exp(x)
        return ex / (1.0 + ex)


def relevance_features(
    content: str,
    sender_id: str,
    *,
    is_at_mention: bool = False,
    is_reply_to_ene: bool = False,
    ene_last_spoke_seconds_ago: float = float("inf"),
    author_ene_interaction_ratio: float = 0.0,
    is_in_ene_thread: bool = False,
    ene_was_last_speaker: bool = False,
) -> dict[str, float]:
    """Extract relevance features for a single message.

    All features normalized to [0, 1]. Cheap to compute — no API calls.

    Args:
        content: Raw message text.
        sender_id: Platform ID of the sender.
        is_at_mention: True if message contains @Ene bot mention.
        is_reply_to_ene: True if Discord reply targets an Ene message.
        ene_last_spoke_seconds_ago: Seconds since Ene's last message in channel.
        author_ene_interaction_ratio: Fraction of author's messages that engaged Ene [0,1].
        is_in_ene_thread: True if message is in a tracked thread with ene_involved.
        ene_was_last_speaker: True if Ene was the most recent speaker before this.

    Returns:
        Dict of feature_name -> value in [0, 1].
    """
    f: dict[str, float] = {}

    # Explicit signals (very strong)
    f["mention"] = 1.0 if is_at_mention else 0.0
    f["reply"] = 1.0 if is_reply_to_ene else 0.0
    f["name"] = 1.0 if bool(_ENE_PATTERN.search(content)) else 0.0

    # Recency: exponential decay, halflife = 120 seconds
    # 1.0 if Ene just spoke, 0.5 after 2 min, ~0 after 10 min
    if ene_last_spoke_seconds_ago < float("inf"):
        _lambda = math.log(2) / 120.0
        f["recency"] = math.exp(-_lambda * max(0.0, ene_last_spoke_seconds_ago))
    else:
        f["recency"] = 0.0

    # Author interaction history
    f["author_hist"] = min(1.0, max(0.0, author_ene_interaction_ratio))

    # Question detection
    stripped = content.strip()
    if stripped.endswith("?"):
        f["question"] = 1.0
    else:
        first_word = stripped.split()[0].lower() if stripped.split() else ""
        q_words = {"who", "what", "where", "when", "why", "how",
                   "can", "could", "would", "should", "do", "does",
                   "is", "are", "will", "did"}
        f["question"] = 0.5 if first_word in q_words else 0.0

    # Thread context
    f["ene_thread"] = 1.0 if is_in_ene_thread else 0.0

    # Adjacency pair: Ene spoke, then this message immediately follows
    # Strong signal when combined with recency < 60s
    if ene_was_last_speaker and ene_last_spoke_seconds_ago < 60:
        f["adjacency"] = math.exp(-ene_last_spoke_seconds_ago / 30.0)
    else:
        f["adjacency"] = 0.0

    return f


def relevance_score(features: dict[str, float]) -> float:
    """Compute P(directed_at_ene) from feature dict.

    Uses Naive Bayes log-odds: score = sigmoid(prior + sum(w_i * f_i)).
    Returns float in [0, 1].
    """
    log_odds = _PRIOR_LOG_ODDS
    for key, weight in _RELEVANCE_WEIGHTS.items():
        log_odds += weight * features.get(key, 0.0)
    return _sigmoid(log_odds)


def classify_relevance(
    content: str,
    sender_id: str,
    *,
    _channel_key: str = "",
    **kwargs,
) -> tuple[str, float, dict[str, float]]:
    """One-call relevance classification.

    Returns:
        (classification, score, features)
        classification: "respond" | "context" | "drop"
        score: P(directed_at_ene) in [0, 1]
        features: the raw feature dict for debugging
    """
    features = relevance_features(content, sender_id, **kwargs)
    score = relevance_score(features)

    if score >= RESPOND_THRESHOLD:
        classification = "respond"
    elif score >= CONTEXT_THRESHOLD:
        classification = "context"
    else:
        classification = "drop"

    # Record classification with full feature breakdown
    if _metrics:
        _metrics.record(
            "scored",
            _channel_key,
            sender=sender_id,
            result=classification,
            confidence=round(score, 4),
            features={k: round(v, 4) for k, v in features.items()},
            raw_log_odds=round(
                _PRIOR_LOG_ODDS + sum(
                    _RELEVANCE_WEIGHTS.get(k, 0) * v
                    for k, v in features.items()
                ),
                4,
            ),
        )

    return classification, score, features


# ═══════════════════════════════════════════════════════════════════════
# PART 3: Conversation State Detector
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class ChannelState:
    """Lightweight per-channel state for relevance scoring.

    Tracks Ene's last activity, per-author interaction rates, and
    message timing for conversation state detection. No LLM calls.

    Memory: ~1KB per channel. Call update() on every message.
    """

    channel_key: str

    # Ene's last message timestamp in this channel
    ene_last_spoke: float = 0.0

    # Was Ene the most recent speaker?
    ene_was_last_speaker: bool = False

    # Per-author interaction tracking
    # author_id -> deque of timestamps when they interacted with Ene
    _author_ene_interactions: dict[str, deque] = field(default_factory=dict)
    # author_id -> total message count (rolling window)
    _author_msg_counts: dict[str, int] = field(default_factory=dict)

    # Message timestamps for rate estimation (last 30 messages)
    _msg_timestamps: deque = field(default_factory=lambda: deque(maxlen=30))

    # Recent speaker order (most recent first)
    _recent_speakers: deque = field(default_factory=lambda: deque(maxlen=10))

    def update(
        self,
        sender_id: str,
        timestamp: float,
        is_ene: bool = False,
        interacted_with_ene: bool = False,
    ) -> None:
        """Update channel state with a new message.

        Args:
            sender_id: Platform ID of the sender.
            timestamp: Unix timestamp of the message.
            is_ene: True if this message is FROM Ene.
            interacted_with_ene: True if this message was directed at Ene
                (determined by relevance classifier or explicit signals).
        """
        self._msg_timestamps.append(timestamp)

        if is_ene:
            self.ene_last_spoke = timestamp
            self.ene_was_last_speaker = True
        else:
            self.ene_was_last_speaker = False
            # Track author message counts
            self._author_msg_counts[sender_id] = (
                self._author_msg_counts.get(sender_id, 0) + 1
            )

        # Track speaker order
        if sender_id in self._recent_speakers:
            self._recent_speakers.remove(sender_id)
        self._recent_speakers.appendleft(sender_id)

        # Track Ene interactions per author
        if interacted_with_ene and not is_ene:
            if sender_id not in self._author_ene_interactions:
                self._author_ene_interactions[sender_id] = deque(maxlen=50)
            self._author_ene_interactions[sender_id].append(timestamp)

    def ene_last_spoke_seconds_ago(self, now: float | None = None) -> float:
        """Seconds since Ene last spoke in this channel."""
        if self.ene_last_spoke == 0.0:
            return float("inf")
        now = now or _time.time()
        return max(0.0, now - self.ene_last_spoke)

    def author_ene_ratio(self, author_id: str) -> float:
        """Fraction of this author's messages that interacted with Ene.

        Returns 0.0 if no history. Capped at 1.0.
        """
        total = self._author_msg_counts.get(author_id, 0)
        if total == 0:
            return 0.0
        interactions = self._author_ene_interactions.get(author_id)
        if not interactions:
            return 0.0
        return min(1.0, len(interactions) / total)

    def estimate_rate(self, now: float | None = None) -> float:
        """Estimate current message rate (messages per minute).

        Uses exponentially weighted intervals. Halflife = 120s.
        """
        now = now or _time.time()
        timestamps = list(self._msg_timestamps)
        if len(timestamps) < 2:
            return 0.0

        alpha = math.log(2) / 120.0
        weighted_interval = 0.0
        weight_sum = 0.0

        for i in range(len(timestamps) - 1):
            delta = timestamps[i + 1] - timestamps[i]
            if delta <= 0:
                continue
            midpoint = (timestamps[i] + timestamps[i + 1]) / 2.0
            weight = math.exp(-alpha * (now - midpoint))
            weighted_interval += weight * delta
            weight_sum += weight

        if weighted_interval <= 0:
            return 0.0
        return (weight_sum / weighted_interval) * 60.0

    def conversation_state(self, now: float | None = None) -> str:
        """Classify conversation state: 'active', 'winding_down', or 'dead'.

        Based on message arrival rate and silence duration.
        """
        now = now or _time.time()
        timestamps = list(self._msg_timestamps)

        if len(timestamps) < 2:
            silence = (now - timestamps[-1]) if timestamps else float("inf")
            return "dead" if silence > 600 else "winding_down"

        rate = self.estimate_rate(now)
        silence = now - timestamps[-1]

        # Expected interval between messages given current rate
        expected = (60.0 / rate) if rate > 0 else float("inf")
        # How many expected intervals of silence have passed?
        intervals_passed = (silence / expected) if expected < float("inf") else float("inf")

        if rate >= 2.0 and intervals_passed < 2.0:
            return "active"
        elif rate >= 0.5 and intervals_passed < 4.0:
            return "active" if intervals_passed < 2.0 else "winding_down"
        elif intervals_passed >= 4.0 or silence > 600:
            return "dead"
        else:
            return "winding_down"


# ═══════════════════════════════════════════════════════════════════════
# PART 4: Convenience — classify with channel state in one call
# ═══════════════════════════════════════════════════════════════════════


def classify_with_state(
    content: str,
    sender_id: str,
    channel_state: ChannelState,
    *,
    is_at_mention: bool = False,
    is_reply_to_ene: bool = False,
    is_in_ene_thread: bool = False,
    now: float | None = None,
) -> tuple[str, float, dict[str, float]]:
    """Full classification using channel state for implicit features.

    This is the main entry point for the daemon/fallback classifier.
    Pulls recency, author history, adjacency from ChannelState.

    Returns:
        (classification, score, features)
    """
    now = now or _time.time()

    return classify_relevance(
        content,
        sender_id,
        _channel_key=channel_state.channel_key,
        is_at_mention=is_at_mention,
        is_reply_to_ene=is_reply_to_ene,
        ene_last_spoke_seconds_ago=channel_state.ene_last_spoke_seconds_ago(now),
        author_ene_interaction_ratio=channel_state.author_ene_ratio(sender_id),
        is_in_ene_thread=is_in_ene_thread,
        ene_was_last_speaker=channel_state.ene_was_last_speaker,
    )
