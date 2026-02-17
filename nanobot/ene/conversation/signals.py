"""Signal scoring functions for conversation thread detection.

Pure, stateless functions. Each takes a ThreadMessage and a Thread
(or PendingMessage) and returns a float score representing how strongly
the message belongs to that thread.

No LLM calls, no embeddings — fast heuristic math only.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import Thread, ThreadMessage, PendingMessage

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
