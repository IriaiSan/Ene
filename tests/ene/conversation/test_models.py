"""Tests for conversation tracker data models."""

import time
import pytest

from nanobot.ene.conversation.models import (
    ACTIVE,
    DEAD,
    RESOLVED,
    STALE,
    THREAD_DEAD_SECONDS,
    THREAD_MAX_MESSAGES,
    THREAD_STALE_SECONDS,
    PendingMessage,
    Thread,
    ThreadMessage,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def make_msg(
    msg_id: str = "111",
    author: str = "TestUser",
    content: str = "hello",
    author_id: str = "discord:123",
    ts: float | None = None,
    reply_to: str | None = None,
    is_reply_to_ene: bool = False,
    classification: str = "respond",
    is_ene: bool = False,
) -> ThreadMessage:
    return ThreadMessage(
        discord_msg_id=msg_id,
        author_name=author,
        author_username=author.lower(),
        author_id=author_id,
        content=content,
        timestamp=ts or time.time(),
        reply_to_msg_id=reply_to,
        is_reply_to_ene=is_reply_to_ene,
        classification=classification,
        is_ene=is_ene,
    )


# ── ThreadMessage tests ─────────────────────────────────────────────────


class TestThreadMessage:
    def test_to_dict_and_back(self):
        msg = make_msg(msg_id="abc123", author="Alice", content="hi there")
        d = msg.to_dict()
        restored = ThreadMessage.from_dict(d)
        assert restored.discord_msg_id == "abc123"
        assert restored.author_name == "Alice"
        assert restored.content == "hi there"

    def test_from_dict_defaults(self):
        """Missing optional fields get sensible defaults."""
        d = {
            "discord_msg_id": "x",
            "author_name": "Bob",
            "author_id": "discord:1",
            "content": "test",
            "timestamp": 12345.0,
        }
        msg = ThreadMessage.from_dict(d)
        assert msg.reply_to_msg_id is None
        assert msg.is_reply_to_ene is False
        assert msg.classification == "context"
        assert msg.is_ene is False
        assert msg.author_username == ""


# ── Thread tests ─────────────────────────────────────────────────────────


class TestThread:
    def test_new_creates_uuid(self):
        t = Thread.new("discord:chan1")
        assert len(t.thread_id) == 36  # UUID4 format
        assert t.channel_key == "discord:chan1"
        assert t.state == ACTIVE

    def test_add_message_updates_metadata(self):
        t = Thread.new("discord:chan1")
        msg = make_msg(msg_id="m1", author_id="discord:100")
        t.add_message(msg)
        assert "discord:100" in t.participants
        assert "m1" in t.discord_msg_ids
        assert t.message_count == 1

    def test_add_message_ene_involved(self):
        t = Thread.new("discord:chan1")
        assert t.ene_involved is False
        msg = make_msg(is_ene=True)
        t.add_message(msg)
        assert t.ene_involved is True

    def test_add_message_respond_classification_marks_ene(self):
        t = Thread.new("discord:chan1")
        msg = make_msg(classification="respond")
        t.add_message(msg)
        assert t.ene_involved is True

    def test_add_message_reply_to_ene_marks_ene(self):
        t = Thread.new("discord:chan1")
        msg = make_msg(is_reply_to_ene=True, classification="context")
        t.add_message(msg)
        assert t.ene_involved is True

    def test_add_message_trims_over_capacity(self):
        t = Thread.new("discord:chan1")
        for i in range(THREAD_MAX_MESSAGES + 10):
            t.add_message(make_msg(msg_id=f"m{i}", content=f"msg {i}"))
        assert len(t.messages) == THREAD_MAX_MESSAGES
        # Oldest messages should be trimmed
        assert t.messages[0].discord_msg_id == "m10"

    def test_is_expired_active_to_stale(self):
        t = Thread.new("discord:chan1")
        t.updated_at = time.time() - THREAD_STALE_SECONDS - 1
        assert t.is_expired()

    def test_is_expired_stale_to_dead(self):
        t = Thread.new("discord:chan1")
        t.state = STALE
        t.updated_at = time.time() - THREAD_DEAD_SECONDS - 1
        assert t.is_expired()

    def test_is_expired_active_not_yet(self):
        t = Thread.new("discord:chan1")
        t.updated_at = time.time()
        assert t.is_expired() is False

    def test_check_resolution_basic(self):
        t = Thread.new("discord:chan1")
        # Need 2 participants and 3 messages
        t.add_message(make_msg(msg_id="1", author_id="discord:a"))
        t.add_message(make_msg(msg_id="2", author_id="discord:b"))
        t.add_message(make_msg(msg_id="3", author_id="discord:a"))
        resolution_msg = make_msg(content="thanks got it", author_id="discord:b")
        assert t.check_resolution(resolution_msg) is True

    def test_check_resolution_too_few_participants(self):
        t = Thread.new("discord:chan1")
        t.add_message(make_msg(msg_id="1", author_id="discord:a"))
        t.add_message(make_msg(msg_id="2", author_id="discord:a"))
        t.add_message(make_msg(msg_id="3", author_id="discord:a"))
        msg = make_msg(content="thanks", author_id="discord:a")
        assert t.check_resolution(msg) is False

    def test_check_resolution_no_pattern_match(self):
        t = Thread.new("discord:chan1")
        t.add_message(make_msg(msg_id="1", author_id="discord:a"))
        t.add_message(make_msg(msg_id="2", author_id="discord:b"))
        t.add_message(make_msg(msg_id="3", author_id="discord:a"))
        msg = make_msg(content="what do you mean?", author_id="discord:b")
        assert t.check_resolution(msg) is False

    def test_serialization_roundtrip(self):
        t = Thread.new("discord:chan1")
        t.add_message(make_msg(msg_id="m1", author_id="discord:100"))
        t.add_message(make_msg(msg_id="m2", author_id="discord:200", is_ene=True))
        t.topic_keywords = ["math", "derivative"]

        d = t.to_dict()
        restored = Thread.from_dict(d)

        assert restored.thread_id == t.thread_id
        assert restored.channel_key == "discord:chan1"
        assert restored.ene_involved is True
        assert len(restored.messages) == 2
        assert restored.messages[1].is_ene is True
        assert "discord:100" in restored.participants
        assert "discord:200" in restored.participants
        assert "m1" in restored.discord_msg_ids
        assert restored.topic_keywords == ["math", "derivative"]

    def test_participant_count(self):
        t = Thread.new("discord:chan1")
        t.add_message(make_msg(author_id="discord:a"))
        t.add_message(make_msg(author_id="discord:b"))
        t.add_message(make_msg(author_id="discord:a"))
        assert t.participant_count == 2

    def test_ene_responded_default_false(self):
        """New threads have ene_responded=False."""
        t = Thread.new("discord:chan1")
        assert t.ene_responded is False

    def test_ene_responded_serialization(self):
        """ene_responded flag persists through serialization."""
        t = Thread.new("discord:chan1")
        t.ene_responded = True
        d = t.to_dict()
        assert d["ene_responded"] is True

        restored = Thread.from_dict(d)
        assert restored.ene_responded is True

    def test_ene_responded_missing_in_dict(self):
        """Old serialized threads without ene_responded default to False."""
        d = Thread.new("discord:chan1").to_dict()
        del d["ene_responded"]
        restored = Thread.from_dict(d)
        assert restored.ene_responded is False


# ── PendingMessage tests ────────────────────────────────────────────────


class TestPendingMessage:
    def test_creation(self):
        msg = make_msg(msg_id="p1")
        pm = PendingMessage(message=msg, channel_key="discord:chan1")
        assert pm.discord_msg_id == "p1"
        assert pm.channel_key == "discord:chan1"
        assert pm.created_at > 0

    def test_auto_msg_id(self):
        msg = make_msg(msg_id="p2")
        pm = PendingMessage(message=msg, channel_key="discord:chan1")
        assert pm.discord_msg_id == "p2"
