"""Tests for the multi-thread context formatter."""

import time
import pytest
from datetime import datetime
from pathlib import Path

from nanobot.bus.events import InboundMessage
from nanobot.ene.conversation.models import (
    ACTIVE,
    STALE,
    Thread,
    ThreadMessage,
    PendingMessage,
)
from nanobot.ene.conversation.formatter import (
    _format_age,
    _format_thread_message,
    build_threaded_context,
    build_single_thread_context,
)


# ── Helpers ──────────────────────────────────────────────────────────────


def make_thread_msg(
    msg_id: str = "111",
    author: str = "TestUser",
    username: str = "testuser",
    content: str = "hello",
    author_id: str = "discord:123",
    ts: float | None = None,
    classification: str = "respond",
    is_ene: bool = False,
) -> ThreadMessage:
    return ThreadMessage(
        discord_msg_id=msg_id,
        author_name=author,
        author_username=username,
        author_id=author_id,
        content=content,
        timestamp=ts or time.time(),
        reply_to_msg_id=None,
        is_reply_to_ene=False,
        classification=classification,
        is_ene=is_ene,
    )


def make_thread(
    channel: str = "discord:chan1",
    ene_involved: bool = True,
    state: str = ACTIVE,
    messages: list[ThreadMessage] | None = None,
    created_offset: float = 0,
) -> Thread:
    t = Thread.new(channel)
    t.state = state
    t.ene_involved = ene_involved
    if created_offset:
        t.created_at = time.time() - created_offset
    if messages:
        for m in messages:
            t.add_message(m)
    return t


def make_inbound(
    msg_id: str = "111",
    sender_id: str = "123",
    content: str = "hello",
    author_name: str = "TestUser",
) -> InboundMessage:
    return InboundMessage(
        channel="discord",
        sender_id=sender_id,
        chat_id="chan1",
        content=content,
        timestamp=datetime.now(),
        metadata={
            "message_id": msg_id,
            "author_name": author_name,
            "username": "testuser",
            "guild_id": "1306235136400035911",
        },
    )


# ── Format age tests ────────────────────────────────────────────────────


class TestFormatAge:
    def test_seconds(self):
        now = time.time()
        assert _format_age(now - 30, now) == "30s ago"

    def test_minutes(self):
        now = time.time()
        assert _format_age(now - 180, now) == "3 min ago"

    def test_hours(self):
        now = time.time()
        assert _format_age(now - 7200, now) == "2h ago"

    def test_days(self):
        now = time.time()
        assert _format_age(now - 172800, now) == "2d ago"


# ── Format thread message tests ─────────────────────────────────────────


class TestFormatThreadMessage:
    def test_basic_format_no_tag(self):
        """Default (with_tag=False): no #msgN prefix, counter unchanged."""
        msg = make_thread_msg(msg_id="m1", author="Alice", username="alice")
        msg_id_map = {}
        line, next_counter = _format_thread_message(msg, 1, msg_id_map)
        assert line == "Alice (@alice): hello"
        assert "#msg1" not in msg_id_map
        assert next_counter == 1  # Counter unchanged

    def test_basic_format_with_tag(self):
        """with_tag=True: #msgN prefix, counter incremented, map populated."""
        msg = make_thread_msg(msg_id="m1", author="Alice", username="alice")
        msg_id_map = {}
        line, next_counter = _format_thread_message(
            msg, 1, msg_id_map, with_tag=True
        )
        assert line == "#msg1 Alice (@alice): hello"
        assert msg_id_map["#msg1"] == "m1"
        assert next_counter == 2

    def test_ene_format(self):
        msg = make_thread_msg(is_ene=True, content="sure I can help")
        msg_id_map = {}
        line, _ = _format_thread_message(msg, 1, msg_id_map, with_tag=True)
        assert line.startswith("#msg1 Ene:")

    def test_ene_format_no_tag(self):
        msg = make_thread_msg(is_ene=True, content="sure I can help")
        msg_id_map = {}
        line, _ = _format_thread_message(msg, 1, msg_id_map)
        assert line == "Ene: sure I can help"

    def test_same_name_and_username(self):
        msg = make_thread_msg(author="alice", username="alice")
        msg_id_map = {}
        line, _ = _format_thread_message(msg, 1, msg_id_map)
        # Should not show (@alice) when name == username
        assert "(@alice)" not in line


# ── Build threaded context tests ─────────────────────────────────────────


class TestBuildThreadedContext:
    def test_single_message_no_threads_fast_path(self):
        """Single respond message + no threads → returns as-is."""
        msg = make_inbound(msg_id="m1", content="hey ene")
        result = build_threaded_context(
            threads={},
            pending=[],
            respond_msgs=[msg],
            context_msgs=[],
            channel_key="discord:chan1",
        )
        # Should be the original message unchanged
        assert result.content == "hey ene"

    def test_ene_thread_section(self):
        """Ene-involved threads appear in active conversations section."""
        msgs = [
            make_thread_msg(msg_id="m1", author="Alice", content="hey ene"),
            make_thread_msg(msg_id="m2", author="Ene", content="hi!", is_ene=True),
        ]
        thread = make_thread(ene_involved=True, messages=msgs, created_offset=120)
        threads = {thread.thread_id: thread}

        respond = [make_inbound(msg_id="m1", content="hey ene")]
        result = build_threaded_context(
            threads=threads,
            pending=[],
            respond_msgs=respond,
            context_msgs=[],
            channel_key="discord:chan1",
        )

        assert "[active conversations" in result.content
        assert "Alice" in result.content
        assert "Ene:" in result.content

    def test_background_thread_section(self):
        """Non-Ene threads appear in background section."""
        msgs = [
            make_thread_msg(msg_id="m1", author="Bob", author_id="discord:200",
                            content="valorant?", classification="context"),
            make_thread_msg(msg_id="m2", author="Carol", author_id="discord:300",
                            content="sure", classification="context"),
        ]
        bg_thread = make_thread(
            ene_involved=False, messages=msgs, created_offset=300, state=STALE
        )

        ene_msgs = [make_thread_msg(msg_id="e1", author="Dad", content="hey ene")]
        ene_thread = make_thread(ene_involved=True, messages=ene_msgs)

        threads = {
            ene_thread.thread_id: ene_thread,
            bg_thread.thread_id: bg_thread,
        }

        respond = [make_inbound(msg_id="e1", content="hey ene")]
        result = build_threaded_context(
            threads=threads,
            pending=[],
            respond_msgs=respond,
            context_msgs=[],
            channel_key="discord:chan1",
        )

        assert "[background conversations" in result.content
        assert "Bob" in result.content
        assert "valorant?" in result.content

    def test_long_thread_windowing(self):
        """Long threads show first 2 + gap + last 6."""
        msgs = [
            make_thread_msg(msg_id=f"m{i}", content=f"message {i}",
                            author_id=f"discord:{100 + i % 2}")
            for i in range(20)
        ]
        thread = make_thread(ene_involved=True, messages=msgs, created_offset=60)
        threads = {thread.thread_id: thread}

        respond = [make_inbound(msg_id="m19")]
        result = build_threaded_context(
            threads=threads,
            pending=[],
            respond_msgs=respond,
            context_msgs=[],
            channel_key="discord:chan1",
        )

        assert "[... " in result.content
        assert "earlier messages omitted" in result.content

    def test_msg_id_map_populated(self):
        """msg_id_map maps #msgN → real Discord IDs (only last non-Ene tagged)."""
        msgs = [
            make_thread_msg(msg_id="discord_001", author="Alice", content="hey"),
            make_thread_msg(msg_id="discord_002", author="Bob", content="hi"),
        ]
        thread = make_thread(ene_involved=True, messages=msgs)
        threads = {thread.thread_id: thread}

        respond = [make_inbound(msg_id="discord_001")]
        result = build_threaded_context(
            threads=threads,
            pending=[],
            respond_msgs=respond,
            context_msgs=[],
            channel_key="discord:chan1",
        )

        msg_map = result.metadata["msg_id_map"]
        # Only the last non-Ene message (Bob, discord_002) should be tagged
        assert "#msg1" in msg_map
        assert msg_map["#msg1"] == "discord_002"

    def test_unthreaded_section(self):
        """Pending messages appear in unthreaded section."""
        pending_msg = make_thread_msg(msg_id="p1", author="Random", content="lol")
        pending = [PendingMessage(
            message=pending_msg,
            channel_key="discord:chan1",
        )]

        respond = [make_inbound(msg_id="r1", content="hey ene")]
        result = build_threaded_context(
            threads={},
            pending=pending,
            respond_msgs=respond,
            context_msgs=[],
            channel_key="discord:chan1",
        )

        assert "[unthreaded]" in result.content
        assert "Random" in result.content

    def test_metadata_contract(self):
        """Output has all required metadata fields."""
        thread = make_thread(
            ene_involved=True,
            messages=[make_thread_msg(msg_id="m1")],
        )
        threads = {thread.thread_id: thread}

        respond = [make_inbound(msg_id="m1")]
        result = build_threaded_context(
            threads=threads,
            pending=[],
            respond_msgs=respond,
            context_msgs=[],
            channel_key="discord:chan1",
        )

        assert result.metadata["debounced"] is True
        assert "debounce_count" in result.metadata
        assert "message_ids" in result.metadata
        assert "msg_id_map" in result.metadata
        assert "thread_count" in result.metadata
        assert "bg_thread_count" in result.metadata

    def test_fallback_when_no_threads(self):
        """When tracker has no threads, produces a basic trace from raw messages."""
        respond = [
            make_inbound(msg_id="r1", content="hey ene", author_name="Alice"),
            make_inbound(msg_id="r2", content="help pls", author_name="Bob"),
        ]
        context = [
            make_inbound(msg_id="c1", content="random stuff", author_name="Carol"),
        ]

        result = build_threaded_context(
            threads={},
            pending=[],
            respond_msgs=respond,
            context_msgs=context,
            channel_key="discord:chan1",
        )

        assert "[conversation trace]" in result.content
        assert "Alice" in result.content
        assert "Bob" in result.content
        assert "[background" in result.content
        assert "Carol" in result.content

    def test_multiple_ene_threads(self):
        """Multiple Ene threads shown, capped at max display."""
        threads = {}
        for i in range(6):
            msgs = [make_thread_msg(msg_id=f"t{i}_m1", content=f"thread {i}")]
            t = make_thread(ene_involved=True, messages=msgs, created_offset=i * 60)
            threads[t.thread_id] = t

        respond = [make_inbound(msg_id="t0_m1")]
        result = build_threaded_context(
            threads=threads,
            pending=[],
            respond_msgs=respond,
            context_msgs=[],
            channel_key="discord:chan1",
        )

        # Should cap at 4 Ene threads
        assert "more threads you're part of" in result.content

    def test_thread_shows_state(self):
        """Thread state (active/stale) shown in header."""
        msgs = [make_thread_msg(msg_id="m1")]
        thread = make_thread(ene_involved=True, messages=msgs, state=STALE)
        threads = {thread.thread_id: thread}

        respond = [make_inbound(msg_id="m1")]
        result = build_threaded_context(
            threads=threads,
            pending=[],
            respond_msgs=respond,
            context_msgs=[],
            channel_key="discord:chan1",
        )

        assert "(stale)" in result.content


# ── Tests for build_single_thread_context (Phase 2.3) ────────────────────


class TestBuildSingleThreadContext:
    """Test per-thread focused context formatting."""

    def test_basic_single_thread(self):
        """Focus thread content shows respond section."""
        msgs = [
            make_thread_msg(msg_id="m1", author="Alice", content="hey ene"),
            make_thread_msg(msg_id="m2", author="Alice", content="can you help?"),
        ]
        thread = make_thread(ene_involved=True, messages=msgs)

        content, msg_id_map, primary = build_single_thread_context(
            focus_thread=thread,
            all_threads={thread.thread_id: thread},
            pending=[],
            channel_key="discord:chan1",
        )

        assert "[your conversation" in content
        assert "Alice" in content
        assert "hey ene" in content
        assert primary == "Alice"

    def test_primary_is_non_ene_speaker(self):
        """Primary author is the most recent non-Ene speaker."""
        msgs = [
            make_thread_msg(msg_id="m1", author="Bob", content="q"),
            make_thread_msg(msg_id="m2", author="Ene", content="a", is_ene=True),
            make_thread_msg(msg_id="m3", author="Bob", content="followup"),
        ]
        thread = make_thread(ene_involved=True, messages=msgs)

        _, _, primary = build_single_thread_context(
            thread, {thread.thread_id: thread}, [], "discord:chan1"
        )
        assert primary == "Bob"

    def test_msg_id_map(self):
        """msg_id_map maps #msgN to Discord message IDs."""
        msgs = [make_thread_msg(msg_id="d_001", author="Alice", content="hello")]
        thread = make_thread(ene_involved=True, messages=msgs)

        _, msg_id_map, _ = build_single_thread_context(
            thread, {thread.thread_id: thread}, [], "discord:chan1"
        )

        assert "#msg1" in msg_id_map
        assert msg_id_map["#msg1"] == "d_001"

    def test_other_threads_as_background(self):
        """Other threads appear in background section."""
        focus_msgs = [make_thread_msg(msg_id="m1", author="Alice", content="main")]
        focus = make_thread(ene_involved=True, messages=focus_msgs)

        bg_msgs = [make_thread_msg(msg_id="m2", author="Charlie", content="side convo")]
        bg = make_thread(ene_involved=False, messages=bg_msgs)

        all_threads = {focus.thread_id: focus, bg.thread_id: bg}
        content, _, _ = build_single_thread_context(
            focus, all_threads, [], "discord:chan1"
        )

        assert "[background" in content
        assert "Charlie" in content

    def test_does_not_mutate_last_shown_index(self):
        """Must NOT change last_shown_index on the focus thread."""
        msgs = [make_thread_msg(msg_id="m1", author="Alice", content="hello")]
        thread = make_thread(ene_involved=True, messages=msgs)
        original = thread.last_shown_index

        build_single_thread_context(
            thread, {thread.thread_id: thread}, [], "discord:chan1"
        )

        assert thread.last_shown_index == original

    def test_follow_up_shows_only_new(self):
        """When last_shown_index > 0, shows only new messages."""
        msgs = [
            make_thread_msg(msg_id="m1", author="Alice", content="old"),
            make_thread_msg(msg_id="m2", author="Alice", content="also old"),
            make_thread_msg(msg_id="m3", author="Alice", content="NEW msg"),
        ]
        thread = make_thread(ene_involved=True, messages=msgs)
        thread.last_shown_index = 2

        content, _, _ = build_single_thread_context(
            thread, {thread.thread_id: thread}, [], "discord:chan1"
        )

        assert "NEW msg" in content
        assert "continued" in content

    def test_excludes_other_channel(self):
        """Threads from other channels don't appear."""
        focus_msgs = [make_thread_msg(msg_id="m1", author="Alice", content="main")]
        focus = make_thread(ene_involved=True, messages=focus_msgs, channel="discord:chan1")

        other_msgs = [make_thread_msg(msg_id="m2", author="Eve", content="other")]
        other = make_thread(ene_involved=True, messages=other_msgs, channel="discord:chan2")

        all_threads = {focus.thread_id: focus, other.thread_id: other}
        content, _, _ = build_single_thread_context(
            focus, all_threads, [], "discord:chan1"
        )

        assert "Eve" not in content
