"""Tests for conversation thread signal scoring functions."""

import time
import pytest

from nanobot.ene.conversation.models import Thread, ThreadMessage, PendingMessage
from nanobot.ene.conversation.signals import (
    extract_keywords,
    score_reply_chain,
    score_mention_affinity,
    score_temporal,
    score_speaker,
    score_lexical,
    compute_thread_score,
    score_against_pending,
    REPLY_CHAIN_WEIGHT,
    MENTION_WEIGHT,
    SPEAKER_WEIGHT,
    LEXICAL_WEIGHT,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def make_msg(
    msg_id: str = "111",
    author: str = "TestUser",
    content: str = "hello",
    author_id: str = "discord:123",
    ts: float | None = None,
    reply_to: str | None = None,
    classification: str = "respond",
) -> ThreadMessage:
    return ThreadMessage(
        discord_msg_id=msg_id,
        author_name=author,
        author_username=author.lower(),
        author_id=author_id,
        content=content,
        timestamp=ts or time.time(),
        reply_to_msg_id=reply_to,
        is_reply_to_ene=False,
        classification=classification,
    )


def make_thread(
    channel: str = "discord:chan1",
    messages: list[ThreadMessage] | None = None,
    keywords: list[str] | None = None,
) -> Thread:
    t = Thread.new(channel)
    if messages:
        for m in messages:
            t.add_message(m)
    if keywords:
        t.topic_keywords = keywords
    return t


# ── Keyword extraction tests ────────────────────────────────────────────


class TestExtractKeywords:
    def test_basic_extraction(self):
        keywords = extract_keywords("What is the derivative of a polynomial function?")
        assert "derivative" in keywords
        assert "polynomial" in keywords
        assert "function" in keywords

    def test_stopwords_removed(self):
        keywords = extract_keywords("I think this is very good and also like it")
        # All stopwords — should return empty or very few
        assert "think" not in keywords
        assert "this" not in keywords
        assert "very" not in keywords

    def test_discord_slang_removed(self):
        keywords = extract_keywords("lol bruh ngl tbh that was fire bro")
        assert "lol" not in keywords
        assert "bruh" not in keywords
        assert "ngl" not in keywords

    def test_max_keywords(self):
        keywords = extract_keywords(
            "python javascript typescript react angular vue svelte",
            max_keywords=3,
        )
        assert len(keywords) <= 3

    def test_empty_input(self):
        assert extract_keywords("") == []

    def test_short_words_ignored(self):
        """Words under 3 chars are filtered out."""
        keywords = extract_keywords("go do it as we me be at")
        assert keywords == []

    def test_frequency_ranking(self):
        keywords = extract_keywords(
            "python python python java java rust",
            max_keywords=3,
        )
        assert keywords[0] == "python"
        assert keywords[1] == "java"


# ── Reply chain scoring ─────────────────────────────────────────────────


class TestScoreReplyChain:
    def test_direct_reply_match(self):
        thread = make_thread(messages=[make_msg(msg_id="m1"), make_msg(msg_id="m2")])
        msg = make_msg(reply_to="m1")
        assert score_reply_chain(msg, thread) == REPLY_CHAIN_WEIGHT

    def test_no_reply(self):
        thread = make_thread(messages=[make_msg(msg_id="m1")])
        msg = make_msg(reply_to=None)
        assert score_reply_chain(msg, thread) == 0.0

    def test_reply_to_different_thread(self):
        thread = make_thread(messages=[make_msg(msg_id="m1")])
        msg = make_msg(reply_to="m999")
        assert score_reply_chain(msg, thread) == 0.0


# ── Mention affinity scoring ────────────────────────────────────────────


class TestScoreMentionAffinity:
    def test_mentions_participant(self):
        thread = make_thread(
            messages=[make_msg(author_id="discord:100", author="Alice")]
        )
        msg = make_msg(content="hey Alice what do you think?")
        resolver = {"discord:100": "Alice"}
        assert score_mention_affinity(msg, thread, resolver) == MENTION_WEIGHT

    def test_no_mention(self):
        thread = make_thread(
            messages=[make_msg(author_id="discord:100", author="Alice")]
        )
        msg = make_msg(content="hello world")
        resolver = {"discord:100": "Alice"}
        assert score_mention_affinity(msg, thread, resolver) == 0.0

    def test_no_resolver(self):
        thread = make_thread(messages=[make_msg()])
        msg = make_msg(content="hey Alice")
        assert score_mention_affinity(msg, thread, None) == 0.0

    def test_short_name_ignored(self):
        """Names under 3 chars should not trigger."""
        thread = make_thread(
            messages=[make_msg(author_id="discord:100")]
        )
        msg = make_msg(content="do it")
        resolver = {"discord:100": "Al"}
        assert score_mention_affinity(msg, thread, resolver) == 0.0

    def test_case_insensitive(self):
        thread = make_thread(
            messages=[make_msg(author_id="discord:100")]
        )
        msg = make_msg(content="ALICE please help")
        resolver = {"discord:100": "Alice"}
        assert score_mention_affinity(msg, thread, resolver) == MENTION_WEIGHT


# ── Temporal scoring ─────────────────────────────────────────────────────


class TestScoreTemporal:
    def test_very_recent(self):
        now = time.time()
        thread = make_thread(messages=[make_msg(ts=now - 5)])
        msg = make_msg(ts=now)
        assert score_temporal(msg, thread) == 0.4

    def test_30_seconds(self):
        now = time.time()
        thread = make_thread(messages=[make_msg(ts=now - 20)])
        msg = make_msg(ts=now)
        assert score_temporal(msg, thread) == 0.3

    def test_2_minutes(self):
        now = time.time()
        thread = make_thread(messages=[make_msg(ts=now - 60)])
        msg = make_msg(ts=now)
        assert score_temporal(msg, thread) == 0.2

    def test_5_minutes(self):
        now = time.time()
        thread = make_thread(messages=[make_msg(ts=now - 200)])
        msg = make_msg(ts=now)
        assert score_temporal(msg, thread) == 0.1

    def test_old_thread(self):
        now = time.time()
        thread = make_thread(messages=[make_msg(ts=now - 600)])
        msg = make_msg(ts=now)
        assert score_temporal(msg, thread) == 0.0


# ── Speaker scoring ──────────────────────────────────────────────────────


class TestScoreSpeaker:
    def test_returning_speaker(self):
        thread = make_thread(
            messages=[make_msg(author_id="discord:100")]
        )
        msg = make_msg(author_id="discord:100")
        assert score_speaker(msg, thread) == SPEAKER_WEIGHT

    def test_new_speaker(self):
        thread = make_thread(
            messages=[make_msg(author_id="discord:100")]
        )
        msg = make_msg(author_id="discord:200")
        assert score_speaker(msg, thread) == 0.0


# ── Lexical scoring ──────────────────────────────────────────────────────


class TestScoreLexical:
    def test_full_overlap(self):
        thread = make_thread(keywords=["python", "derivative", "function"])
        msg = make_msg(content="the derivative of a python function")
        score = score_lexical(msg, thread)
        assert score > 0.0

    def test_no_overlap(self):
        thread = make_thread(keywords=["python", "derivative", "function"])
        msg = make_msg(content="anyone want to play valorant tonight?")
        assert score_lexical(msg, thread) == 0.0

    def test_empty_keywords(self):
        thread = make_thread(keywords=[])
        msg = make_msg(content="hello world test")
        assert score_lexical(msg, thread) == 0.0

    def test_empty_message(self):
        thread = make_thread(keywords=["python"])
        msg = make_msg(content="ok")
        assert score_lexical(msg, thread) == 0.0

    def test_partial_overlap(self):
        thread = make_thread(keywords=["python", "java", "rust", "golang", "cpp"])
        msg = make_msg(content="I prefer python over rust")
        score = score_lexical(msg, thread)
        assert score > 0.0
        assert score <= LEXICAL_WEIGHT


# ── Combined scoring ─────────────────────────────────────────────────────


class TestComputeThreadScore:
    def test_reply_always_passes_threshold(self):
        thread = make_thread(messages=[make_msg(msg_id="m1")])
        msg = make_msg(reply_to="m1")
        score = compute_thread_score(msg, thread)
        assert score >= 0.5  # Above assignment threshold

    def test_speaker_plus_temporal_passes(self):
        now = time.time()
        thread = make_thread(messages=[make_msg(author_id="discord:100", ts=now - 5)])
        msg = make_msg(author_id="discord:100", ts=now)
        score = compute_thread_score(msg, thread)
        # speaker (0.4) + temporal (0.4) = 0.8
        assert score >= 0.5

    def test_temporal_alone_below_threshold(self):
        now = time.time()
        thread = make_thread(messages=[make_msg(author_id="discord:100", ts=now - 5)])
        msg = make_msg(author_id="discord:999", ts=now, content="unrelated stuff here")
        score = compute_thread_score(msg, thread)
        # temporal (0.4) only — new speaker, no lexical, no reply
        assert score < 0.5


# ── Pending message scoring ──────────────────────────────────────────────


class TestScoreAgainstPending:
    def test_reply_to_pending(self):
        pm_msg = make_msg(msg_id="p1", author_id="discord:100")
        pm = PendingMessage(message=pm_msg, channel_key="discord:chan1")
        msg = make_msg(reply_to="p1")
        score = score_against_pending(msg, pm)
        assert score >= REPLY_CHAIN_WEIGHT

    def test_same_speaker_recent(self):
        now = time.time()
        pm_msg = make_msg(msg_id="p1", author_id="discord:100", ts=now - 3)
        pm = PendingMessage(message=pm_msg, channel_key="discord:chan1")
        msg = make_msg(author_id="discord:100", ts=now)
        score = score_against_pending(msg, pm)
        # speaker (0.4) + temporal (0.4) = 0.8
        assert score >= 0.5

    def test_unrelated_pending(self):
        now = time.time()
        pm_msg = make_msg(
            msg_id="p1",
            author_id="discord:100",
            content="python tutorial",
            ts=now - 400,
        )
        pm = PendingMessage(message=pm_msg, channel_key="discord:chan1")
        msg = make_msg(
            author_id="discord:999",
            content="valorant ranked?",
            ts=now,
        )
        score = score_against_pending(msg, pm)
        assert score < 0.5
