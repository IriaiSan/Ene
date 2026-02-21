"""Tests for the conversation tracker core engine."""

import time
import pytest
from pathlib import Path
from datetime import datetime
from unittest.mock import MagicMock

from nanobot.bus.events import InboundMessage
from nanobot.ene.conversation.models import (
    ACTIVE,
    DEAD,
    RESOLVED,
    STALE,
    THREAD_DEAD_SECONDS,
    THREAD_STALE_SECONDS,
    Thread,
    ThreadMessage,
)
from nanobot.ene.conversation.tracker import ConversationTracker


# ── Helpers ──────────────────────────────────────────────────────────────


def make_inbound(
    msg_id: str = "111",
    sender_id: str = "123",
    chat_id: str = "chan1",
    content: str = "hello",
    author_name: str = "TestUser",
    username: str = "testuser",
    reply_to: str | None = None,
    is_reply_to_ene: bool = False,
    channel: str = "discord",
) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id=sender_id,
        chat_id=chat_id,
        content=content,
        timestamp=datetime.now(),
        metadata={
            "message_id": msg_id,
            "author_name": author_name,
            "username": username,
            "reply_to": reply_to,
            "is_reply_to_ene": is_reply_to_ene,
            "guild_id": "1306235136400035911",
        },
    )


# ── Tests ────────────────────────────────────────────────────────────────


class TestConversationTracker:
    @pytest.fixture
    def tracker(self, tmp_path: Path) -> ConversationTracker:
        t = ConversationTracker(thread_dir=tmp_path / "threads")
        t._storage.ensure_dirs()
        return t

    def test_ingest_single_message_goes_pending(self, tracker: ConversationTracker):
        """Single message with no existing threads → pending."""
        msg = make_inbound(msg_id="m1", content="hello ene")
        tracker.ingest_batch([msg], [], "discord:chan1")
        assert len(tracker._threads) == 0
        assert len(tracker._pending) == 1

    def test_two_messages_same_speaker_forms_thread(self, tracker: ConversationTracker):
        """Two messages from same speaker within seconds → thread formed."""
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hey ene")
        msg2 = make_inbound(msg_id="m2", sender_id="100", content="help me with math")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")
        # msg1 → pending, msg2 scores against pending (speaker + temporal) → promoted
        assert len(tracker._threads) == 1
        assert len(tracker._pending) == 0
        thread = list(tracker._threads.values())[0]
        assert thread.message_count == 2

    def test_reply_chain_creates_thread(self, tracker: ConversationTracker):
        """Message replying to a pending message → thread formed."""
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hello")
        tracker.ingest_batch([msg1], [], "discord:chan1")
        assert len(tracker._pending) == 1

        msg2 = make_inbound(
            msg_id="m2", sender_id="200", content="hi back",
            reply_to="m1",
        )
        tracker.ingest_batch([msg2], [], "discord:chan1")
        assert len(tracker._threads) == 1
        assert len(tracker._pending) == 0

    def test_reply_chain_joins_existing_thread(self, tracker: ConversationTracker):
        """Reply to a message in an existing thread → joins that thread."""
        # Create thread
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hey ene")
        msg2 = make_inbound(msg_id="m2", sender_id="100", content="math help pls")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")
        thread = list(tracker._threads.values())[0]
        assert thread.message_count == 2

        # Reply to m1 → joins the thread
        msg3 = make_inbound(
            msg_id="m3", sender_id="200", content="sure what math?",
            reply_to="m1",
        )
        tracker.ingest_batch([msg3], [], "discord:chan1")
        assert len(tracker._threads) == 1
        thread = list(tracker._threads.values())[0]
        assert thread.message_count == 3

    def test_context_messages_tracked(self, tracker: ConversationTracker):
        """Context messages from different speakers go to pending separately."""
        respond = make_inbound(msg_id="r1", sender_id="100", content="hey ene")
        context = make_inbound(msg_id="c1", sender_id="200", content="anyone wanna game?")
        tracker.ingest_batch([respond], [context], "discord:chan1")
        # Different speakers, unrelated content — temporal alone (0.4) < threshold (0.5)
        # Both go to pending
        assert len(tracker._pending) == 2

    def test_lurk_messages_update_threads(self, tracker: ConversationTracker):
        """Lurked context-only messages still update thread state."""
        # Create a thread first
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hello")
        msg2 = make_inbound(msg_id="m2", sender_id="100", content="world")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")
        thread = list(tracker._threads.values())[0]
        assert thread.message_count == 2

        # Lurk message from same speaker → joins thread
        ctx = make_inbound(msg_id="m3", sender_id="100", content="more stuff")
        tracker.ingest_batch([], [ctx], "discord:chan1")
        assert thread.message_count == 3

    def test_separate_conversations_form_separate_threads(self, tracker: ConversationTracker):
        """Unrelated messages from different speakers → separate threads."""
        # First pair: math conversation
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="whats the derivative of x squared")
        tracker.ingest_batch([msg1], [], "discord:chan1")

        msg2 = make_inbound(
            msg_id="m2", sender_id="200", content="its 2x",
            reply_to="m1",
        )
        tracker.ingest_batch([msg2], [], "discord:chan1")

        # Second pair: gaming conversation (different topic, different speakers)
        msg3 = make_inbound(msg_id="m3", sender_id="300", content="anyone wanna play valorant tonight")
        tracker.ingest_batch([msg3], [], "discord:chan1")

        msg4 = make_inbound(
            msg_id="m4", sender_id="400", content="sure what rank are you",
            reply_to="m3",
        )
        tracker.ingest_batch([msg4], [], "discord:chan1")

        assert len(tracker._threads) == 2

    def test_tick_states_active_to_stale(self, tracker: ConversationTracker):
        """Threads go stale after THREAD_STALE_SECONDS."""
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hey")
        msg2 = make_inbound(msg_id="m2", sender_id="100", content="yo")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")
        thread = list(tracker._threads.values())[0]
        assert thread.state == ACTIVE

        # Age the thread
        thread.updated_at = time.time() - THREAD_STALE_SECONDS - 1
        tracker.tick_states()
        assert thread.state == STALE

    def test_tick_states_stale_to_dead(self, tracker: ConversationTracker):
        """Stale threads go dead after THREAD_DEAD_SECONDS."""
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hey")
        msg2 = make_inbound(msg_id="m2", sender_id="100", content="yo")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")
        thread_id = list(tracker._threads.keys())[0]
        thread = tracker._threads[thread_id]

        thread.state = STALE
        thread.updated_at = time.time() - THREAD_DEAD_SECONDS - 1
        dead = tracker.tick_states()

        assert len(dead) == 1
        assert thread_id not in tracker._threads

    def test_resolution_detection(self, tracker: ConversationTracker):
        """Thread resolves when a resolution message is detected."""
        # Build a thread with 2+ participants and 3+ messages via reply chains
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="whats 2+2 ene")
        tracker.ingest_batch([msg1], [], "discord:chan1")

        msg2 = make_inbound(msg_id="m2", sender_id="200", content="its 4", reply_to="m1")
        tracker.ingest_batch([msg2], [], "discord:chan1")

        msg3 = make_inbound(msg_id="m3", sender_id="100", content="thanks got it", reply_to="m2")
        tracker.ingest_batch([msg3], [], "discord:chan1")

        thread = list(tracker._threads.values())[0]
        assert thread.state == RESOLVED

    def test_stale_thread_reactivated(self, tracker: ConversationTracker):
        """A stale thread gets reactivated when a new matching message arrives."""
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hello")
        msg2 = make_inbound(msg_id="m2", sender_id="100", content="world")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")

        thread = list(tracker._threads.values())[0]
        thread.state = STALE

        # New message from same speaker → should reactivate
        msg3 = make_inbound(msg_id="m3", sender_id="100", content="back again")
        tracker.ingest_batch([msg3], [], "discord:chan1")
        assert thread.state == ACTIVE

    def test_save_and_load_state(self, tracker: ConversationTracker):
        """Thread state persists across save/load."""
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hello")
        msg2 = make_inbound(msg_id="m2", sender_id="100", content="world")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")

        tracker.save_state()

        # Create a new tracker loading from same dir
        tracker2 = ConversationTracker(thread_dir=tracker._storage._dir)
        tracker2.load_state()

        assert len(tracker2._threads) == 1
        thread = list(tracker2._threads.values())[0]
        assert thread.message_count == 2

    def test_mark_ene_responded(self, tracker: ConversationTracker):
        """mark_ene_responded sets ene_involved on the right thread."""
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hello")
        msg2 = make_inbound(msg_id="m2", sender_id="100", content="ene help")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")

        thread = list(tracker._threads.values())[0]

        # Simulate Ene responding
        response_msg = InboundMessage(
            channel="discord",
            sender_id="100",
            chat_id="chan1",
            content="how can I help",
            metadata={"message_ids": ["m1"]},
        )
        tracker.mark_ene_responded(response_msg)
        assert thread.ene_involved is True

    def test_build_context_returns_inbound_message(self, tracker: ConversationTracker):
        """build_context returns an InboundMessage with correct metadata."""
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hey ene help")
        msg2 = make_inbound(msg_id="m2", sender_id="200", content="yea ene pls")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")

        result = tracker.build_context([msg1, msg2], [], "discord:chan1")

        assert result.channel == "discord"
        assert "msg_id_map" in result.metadata
        assert result.metadata["debounced"] is True
        assert result.metadata["debounce_count"] == 2

    def test_get_stats(self, tracker: ConversationTracker):
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hey")
        msg2 = make_inbound(msg_id="m2", sender_id="100", content="yo")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")

        stats = tracker.get_stats()
        assert stats["total_threads"] == 1
        assert stats["pending_messages"] == 0

    def test_expired_pending_cleaned(self, tracker: ConversationTracker):
        """Pending messages older than stale threshold are cleaned up."""
        msg = make_inbound(msg_id="p1", sender_id="100", content="old message")
        tracker.ingest_batch([msg], [], "discord:chan1")
        assert len(tracker._pending) == 1

        # Age the pending message
        tracker._pending[0].created_at = time.time() - THREAD_STALE_SECONDS - 1
        tracker.tick_states()
        assert len(tracker._pending) == 0

    def test_cross_channel_isolation(self, tracker: ConversationTracker):
        """Threads in different channels are isolated."""
        msg1 = make_inbound(msg_id="m1", sender_id="100", chat_id="chan1", content="hello ene")
        tracker.ingest_batch([msg1], [], "discord:chan1")

        msg2 = make_inbound(msg_id="m2", sender_id="100", chat_id="chan2", content="hello ene")
        tracker.ingest_batch([msg2], [], "discord:chan2")

        # Each should be pending in its own channel
        assert len(tracker._pending) == 2
        assert tracker._pending[0].channel_key == "discord:chan1"
        assert tracker._pending[1].channel_key == "discord:chan2"

    def test_mark_ene_responded_sets_ene_responded_flag(self, tracker: ConversationTracker):
        """mark_ene_responded sets both ene_involved AND ene_responded."""
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hello")
        msg2 = make_inbound(msg_id="m2", sender_id="100", content="ene help")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")

        thread = list(tracker._threads.values())[0]
        assert thread.ene_responded is False

        response_msg = InboundMessage(
            channel="discord",
            sender_id="100",
            chat_id="chan1",
            content="how can I help",
            metadata={"message_ids": ["m1"]},
        )
        tracker.mark_ene_responded(response_msg)
        assert thread.ene_involved is True
        assert thread.ene_responded is True


class TestBatchParticipantIds:
    """Tests for get_batch_participant_ids() — Scene Brief participant extraction."""

    @pytest.fixture
    def tracker(self, tmp_path: Path) -> ConversationTracker:
        t = ConversationTracker(thread_dir=tmp_path / "threads")
        t._storage.ensure_dirs()
        return t

    def test_single_respond_message(self, tracker):
        """Single respond message → its sender as participant."""
        msg = make_inbound(msg_id="m1", sender_id="100")
        ids = tracker.get_batch_participant_ids([msg], [], "discord:chan1")
        assert ids == ["discord:100"]

    def test_respond_and_context_messages(self, tracker):
        """Respond senders come first, then context senders."""
        respond = make_inbound(msg_id="r1", sender_id="100")
        context = make_inbound(msg_id="c1", sender_id="200")
        ids = tracker.get_batch_participant_ids([respond], [context], "discord:chan1")
        assert ids == ["discord:100", "discord:200"]

    def test_dedup_across_respond_and_context(self, tracker):
        """Same sender in both respond and context → deduplicated."""
        respond = make_inbound(msg_id="r1", sender_id="100")
        context = make_inbound(msg_id="c1", sender_id="100")
        ids = tracker.get_batch_participant_ids([respond], [context], "discord:chan1")
        assert ids == ["discord:100"]

    def test_includes_active_thread_participants(self, tracker):
        """Participants from active ene-involved threads are included."""
        # Create a thread and mark ene_involved
        msg1 = make_inbound(msg_id="m1", sender_id="300", content="hello")
        msg2 = make_inbound(msg_id="m2", sender_id="300", content="world")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")
        thread = list(tracker._threads.values())[0]
        thread.ene_involved = True

        # New batch from a different sender
        respond = make_inbound(msg_id="r1", sender_id="100")
        ids = tracker.get_batch_participant_ids([respond], [], "discord:chan1")

        assert "discord:100" in ids
        assert "discord:300" in ids
        # Respond sender should come first
        assert ids.index("discord:100") < ids.index("discord:300")

    def test_excludes_different_channel_threads(self, tracker):
        """Thread participants from other channels are not included."""
        msg1 = make_inbound(msg_id="m1", sender_id="300", chat_id="chan2", content="hello")
        msg2 = make_inbound(msg_id="m2", sender_id="300", chat_id="chan2", content="world")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan2")
        thread = list(tracker._threads.values())[0]
        thread.ene_involved = True

        respond = make_inbound(msg_id="r1", sender_id="100", chat_id="chan1")
        ids = tracker.get_batch_participant_ids([respond], [], "discord:chan1")

        assert ids == ["discord:100"]
        assert "discord:300" not in ids

    def test_multiple_respond_senders_ordered(self, tracker):
        """Multiple respond senders maintain order."""
        r1 = make_inbound(msg_id="r1", sender_id="100")
        r2 = make_inbound(msg_id="r2", sender_id="200")
        r3 = make_inbound(msg_id="r3", sender_id="300")

        ids = tracker.get_batch_participant_ids([r1, r2, r3], [], "discord:chan1")
        assert ids == ["discord:100", "discord:200", "discord:300"]


# ── Phase 1: commit_shown_indices tests ──────────────────────────────────


class TestCommitShownIndices:
    """Phase 1: Deferred last_shown_index updates via commit_shown_indices()."""

    @pytest.fixture
    def tracker(self, tmp_path: Path) -> ConversationTracker:
        t = ConversationTracker(thread_dir=tmp_path / "threads")
        t._storage.ensure_dirs()
        return t

    def test_commit_updates_thread_index(self, tracker):
        """commit_shown_indices advances last_shown_index on matching threads."""
        msg1 = make_inbound(msg_id="m1", sender_id="100", content="hey")
        msg2 = make_inbound(msg_id="m2", sender_id="100", content="there")
        tracker.ingest_batch([msg1, msg2], [], "discord:chan1")

        thread = list(tracker._threads.values())[0]
        thread_id = thread.thread_id
        assert thread.last_shown_index == 0

        tracker.commit_shown_indices({thread_id: 2})
        assert thread.last_shown_index == 2

    def _make_thread_in_tracker(self, tracker, n_messages=2, sender_id="100"):
        """Helper: create a thread directly in tracker state."""
        from nanobot.ene.conversation.models import Thread, ThreadMessage
        import time
        thread = Thread.new("discord:chan1")
        for i in range(n_messages):
            tm = ThreadMessage(
                discord_msg_id=f"m{i}_{sender_id}",
                author_name="TestUser",
                author_username="testuser",
                author_id=f"discord:{sender_id}",
                content=f"message {i}",
                timestamp=time.time(),
                reply_to_msg_id=None,
                is_reply_to_ene=False,
                classification="respond",
                is_ene=False,
            )
            thread.add_message(tm)
        tracker._threads[thread.thread_id] = thread
        return thread

    def test_commit_does_not_regress_index(self, tracker):
        """commit_shown_indices never moves index backward."""
        thread = self._make_thread_in_tracker(tracker, n_messages=2)
        thread.last_shown_index = 5  # Already advanced

        tracker.commit_shown_indices({thread.thread_id: 3})
        assert thread.last_shown_index == 5  # Stayed at 5, not regressed

    def test_commit_ignores_unknown_threads(self, tracker):
        """commit_shown_indices silently skips unknown thread IDs."""
        # Should not raise
        tracker.commit_shown_indices({"nonexistent_thread_id": 10})

    def test_commit_empty_dict(self, tracker):
        """commit_shown_indices with empty dict is a no-op."""
        tracker.commit_shown_indices({})
        # No crash, no state change

    def test_commit_sets_dirty_flag(self, tracker):
        """commit_shown_indices sets _dirty when indices change."""
        thread = self._make_thread_in_tracker(tracker, n_messages=2)
        tracker._dirty = False  # Reset

        tracker.commit_shown_indices({thread.thread_id: 2})
        assert tracker._dirty is True

    def test_commit_does_not_set_dirty_on_no_change(self, tracker):
        """commit_shown_indices does NOT set _dirty when no index actually changes."""
        tracker._dirty = False
        tracker.commit_shown_indices({"nonexistent": 5})
        assert tracker._dirty is False

    def test_commit_multiple_threads(self, tracker):
        """commit_shown_indices updates multiple threads at once."""
        t1 = self._make_thread_in_tracker(tracker, n_messages=3, sender_id="100")
        t2 = self._make_thread_in_tracker(tracker, n_messages=2, sender_id="200")

        tracker.commit_shown_indices({
            t1.thread_id: 3,
            t2.thread_id: 2,
        })

        assert t1.last_shown_index == 3
        assert t2.last_shown_index == 2


# ── Phase 4: add_ene_response tests ─────────────────────────────────────


class TestAddEneResponse:
    """Phase 4: add_ene_response stores content and no longer has XML guard."""

    @pytest.fixture
    def tracker(self, tmp_path: Path) -> ConversationTracker:
        t = ConversationTracker(thread_dir=tmp_path / "threads")
        t._storage.ensure_dirs()
        return t

    def _setup_thread_with_msg(self, tracker, msg_id="m1"):
        """Create a thread and map a message ID to it."""
        from nanobot.ene.conversation.models import Thread, ThreadMessage
        import time as _t
        thread = Thread.new("discord:chan1")
        tm = ThreadMessage(
            discord_msg_id=msg_id,
            author_name="TestUser",
            author_username="testuser",
            author_id="discord:123",
            content="hello",
            timestamp=_t.time(),
            reply_to_msg_id=None,
            is_reply_to_ene=False,
            classification="respond",
            is_ene=False,
        )
        thread.add_message(tm)
        tracker._threads[thread.thread_id] = thread
        tracker._msg_to_thread[msg_id] = thread.thread_id
        return thread

    def test_stores_clean_content(self, tracker):
        """add_ene_response adds Ene's message to the correct thread."""
        thread = self._setup_thread_with_msg(tracker, "m1")
        msg = make_inbound(msg_id="m1", content="hello")

        tracker.add_ene_response(msg, "hi there!")

        ene_msgs = [m for m in thread.messages if m.is_ene]
        assert len(ene_msgs) == 1
        assert ene_msgs[0].content == "hi there!"
        assert ene_msgs[0].author_name == "Ene"

    def test_rejects_empty(self, tracker):
        """add_ene_response silently skips empty content."""
        thread = self._setup_thread_with_msg(tracker, "m1")
        msg = make_inbound(msg_id="m1", content="hello")

        tracker.add_ene_response(msg, "")

        ene_msgs = [m for m in thread.messages if m.is_ene]
        assert len(ene_msgs) == 0

    def test_rejects_none(self, tracker):
        """add_ene_response silently skips None content."""
        thread = self._setup_thread_with_msg(tracker, "m1")
        msg = make_inbound(msg_id="m1", content="hello")

        tracker.add_ene_response(msg, None)

        ene_msgs = [m for m in thread.messages if m.is_ene]
        assert len(ene_msgs) == 0

    def test_allows_function_word(self, tracker):
        """Content mentioning 'function' is NOT rejected (old XML guard removed)."""
        thread = self._setup_thread_with_msg(tracker, "m1")
        msg = make_inbound(msg_id="m1", content="hello")

        tracker.add_ene_response(msg, "that function works perfectly")

        ene_msgs = [m for m in thread.messages if m.is_ene]
        assert len(ene_msgs) == 1
        assert "function works" in ene_msgs[0].content

    def test_allows_discord_formatting(self, tracker):
        """Discord formatting like <@123> is NOT rejected (old XML guard removed)."""
        thread = self._setup_thread_with_msg(tracker, "m1")
        msg = make_inbound(msg_id="m1", content="hello")

        tracker.add_ene_response(msg, "Hey <@123456>, check this out!")

        ene_msgs = [m for m in thread.messages if m.is_ene]
        assert len(ene_msgs) == 1
        assert "<@123456>" in ene_msgs[0].content

    def test_no_matching_thread(self, tracker):
        """add_ene_response with no matching thread is a no-op."""
        msg = make_inbound(msg_id="unknown_msg", content="hello")
        # No thread mapped to this msg_id
        tracker.add_ene_response(msg, "hi there!")
        # Just shouldn't crash
