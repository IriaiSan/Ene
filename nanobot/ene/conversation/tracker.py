"""Core conversation tracking engine.

Assigns incoming messages to threads, manages thread lifecycle,
and delegates context formatting to the formatter module.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import TYPE_CHECKING

from loguru import logger

from .models import (
    ACTIVE,
    ASSIGNMENT_THRESHOLD,
    DEAD,
    RESOLVED,
    STALE,
    SHIFT_MARKER_PATTERN,
    THREAD_DEAD_SECONDS,
    THREAD_MAX_ACTIVE_PER_CHANNEL,
    THREAD_STALE_SECONDS,
    PendingMessage,
    Thread,
    ThreadMessage,
)
from .signals import (
    ChannelState,
    compute_thread_score,
    extract_keywords,
    score_against_pending,
)
from .storage import ThreadStorage
from .formatter import build_threaded_context

if TYPE_CHECKING:
    from nanobot.bus.events import InboundMessage
    from nanobot.ene.social.person import PersonRegistry


def _sanitize_dad_ids(content: str, caller_id: str) -> str:
    """Import and call the sanitizer from loop.py if available."""
    try:
        from nanobot.agent.loop import _sanitize_dad_ids as sanitize

        return sanitize(content, caller_id)
    except ImportError:
        return content


class ConversationTracker:
    """Core engine for thread detection and tracking.

    Maintains in-memory thread state with periodic disk persistence.
    Called from AgentLoop._flush_debounce() to replace flat merge
    with thread-aware context.
    """

    def __init__(self, thread_dir: Path, social_registry: PersonRegistry | None = None) -> None:
        self._storage = ThreadStorage(thread_dir)
        self._social_registry = social_registry

        # In-memory state
        self._threads: dict[str, Thread] = {}  # thread_id -> Thread
        self._msg_to_thread: dict[str, str] = {}  # discord_msg_id -> thread_id
        self._pending: list[PendingMessage] = []  # Single messages waiting for a match
        self._pending_by_msg_id: dict[str, int] = {}  # discord_msg_id -> index in _pending

        # Dirty flag for lazy saves
        self._dirty = False

        # Per-channel state for math-based relevance scoring (no LLM)
        self._channel_states: dict[str, ChannelState] = {}

    def set_social_registry(self, registry: PersonRegistry | None) -> None:
        """Inject social registry for display name resolution."""
        self._social_registry = registry

    def get_channel_state(self, channel_key: str) -> ChannelState:
        """Get or create per-channel state for math-based classification.

        The ChannelState tracks Ene's last activity, per-author interaction
        history, message rate, and conversation state — all without LLM calls.
        """
        if channel_key not in self._channel_states:
            self._channel_states[channel_key] = ChannelState(channel_key)
        return self._channel_states[channel_key]

    # ── Persistence ──────────────────────────────────────────────────

    def load_state(self) -> None:
        """Load thread state from disk."""
        self._storage.ensure_dirs()
        self._threads, self._pending = self._storage.load_active()

        # Rebuild msg_id index
        self._msg_to_thread.clear()
        for tid, thread in self._threads.items():
            for msg_id in thread.discord_msg_ids:
                self._msg_to_thread[msg_id] = tid

        # Rebuild pending index
        self._pending_by_msg_id.clear()
        for i, pm in enumerate(self._pending):
            if pm.discord_msg_id:
                self._pending_by_msg_id[pm.discord_msg_id] = i

        # On restart, mark all loaded threads as fully shown.
        # The previous session's history already contains these messages —
        # re-showing them would cause Ene to re-respond to old context.
        for thread in self._threads.values():
            if thread.last_shown_index == 0 and thread.messages:
                thread.last_shown_index = len(thread.messages)
                self._dirty = True
            # Also: if last_shown_index is somehow stale (> messages), clamp it
            elif thread.last_shown_index > len(thread.messages):
                thread.last_shown_index = len(thread.messages)
                self._dirty = True

        # Expire anything that went stale during downtime
        self.tick_states()

        logger.info(
            f"Conversation tracker loaded: {len(self._threads)} threads, "
            f"{len(self._pending)} pending"
        )

    def save_state(self) -> None:
        """Save current state to disk."""
        try:
            self._storage.save_active(self._threads, self._pending)
            self._dirty = False
        except Exception as e:
            logger.error(f"Failed to save conversation tracker state: {e}")

    def save_if_dirty(self) -> None:
        """Save only if state has changed since last save."""
        if self._dirty:
            self.save_state()

    # ── Name resolution ──────────────────────────────────────────────

    def _get_name_resolver(self) -> dict[str, str]:
        """Build platform_id -> display_name mapping from social registry."""
        resolver: dict[str, str] = {}
        if not self._social_registry:
            return resolver
        try:
            for person in self._social_registry.list_all():
                for pid in person.platform_ids:
                    resolver[pid] = person.display_name
        except Exception:
            pass
        return resolver

    # ── Message wrapping ─────────────────────────────────────────────

    @staticmethod
    def _wrap_message(
        msg: InboundMessage,
        classification: str,
    ) -> ThreadMessage:
        """Convert an InboundMessage to a ThreadMessage."""
        caller_id = f"{msg.channel}:{msg.sender_id}"
        content = _sanitize_dad_ids(msg.content, caller_id)

        return ThreadMessage(
            discord_msg_id=msg.metadata.get("message_id", ""),
            author_name=msg.metadata.get("author_name", msg.sender_id),
            author_username=msg.metadata.get("username", ""),
            author_id=caller_id,
            content=content,
            timestamp=msg.timestamp.timestamp() if hasattr(msg.timestamp, "timestamp") else time.time(),
            reply_to_msg_id=msg.metadata.get("reply_to"),
            is_reply_to_ene=msg.metadata.get("is_reply_to_ene", False),
            classification=classification,
            is_ene=False,
        )

    # ── Thread assignment ────────────────────────────────────────────

    def _assign_message(
        self,
        tm: ThreadMessage,
        channel_key: str,
        name_resolver: dict[str, str],
    ) -> tuple[str | None, bool]:
        """Assign a message to a thread.

        Returns (thread_id, already_added):
        - thread_id: the thread it was assigned to, or None for pending
        - already_added: True if the message was already added to the thread
          (e.g., during pending promotion), so the caller should NOT add it again.

        Algorithm:
        1. Fast path: explicit reply-to resolves to a thread instantly
        2. Fast path: reply-to resolves to a pending message → promote to thread
        3. Score against all active/stale threads for this channel
        4. Score against pending messages
        5. If best score >= threshold → assign
        6. Otherwise → add to pending
        """
        # Fast path: reply-to -> thread
        if tm.reply_to_msg_id and tm.reply_to_msg_id in self._msg_to_thread:
            return self._msg_to_thread[tm.reply_to_msg_id], False

        # Fast path: reply-to -> pending message (promote to thread)
        if tm.reply_to_msg_id and tm.reply_to_msg_id in self._pending_by_msg_id:
            idx = self._pending_by_msg_id[tm.reply_to_msg_id]
            if idx < len(self._pending):
                pending = self._pending[idx]
                return self._promote_pending(pending, tm, channel_key), True

        # Score against active threads
        channel_threads = [
            t
            for t in self._threads.values()
            if t.channel_key == channel_key and t.state in (ACTIVE, STALE)
        ]

        best_thread_id: str | None = None
        best_score = 0.0

        for t in channel_threads:
            score = compute_thread_score(tm, t, name_resolver)
            if score > best_score:
                best_score = score
                best_thread_id = t.thread_id

        # Score against pending messages
        best_pending_idx: int | None = None
        best_pending_score = 0.0

        for i, pm in enumerate(self._pending):
            if pm.channel_key != channel_key:
                continue
            score = score_against_pending(tm, pm, name_resolver)
            if score > best_pending_score:
                best_pending_score = score
                best_pending_idx = i

        # Decide: thread vs pending vs new pending
        if best_score >= ASSIGNMENT_THRESHOLD and best_score >= best_pending_score:
            return best_thread_id, False

        if best_pending_score >= ASSIGNMENT_THRESHOLD and best_pending_idx is not None:
            pending = self._pending[best_pending_idx]
            return self._promote_pending(pending, tm, channel_key), True

        # No match — add to pending
        self._add_pending(tm, channel_key)
        return None, False

    def _promote_pending(
        self,
        pending: PendingMessage,
        new_msg: ThreadMessage,
        channel_key: str,
    ) -> str:
        """Promote a pending message to a full thread with the new message."""
        thread = Thread.new(channel_key)
        thread.created_at = pending.message.timestamp
        thread.add_message(pending.message)
        thread.add_message(new_msg)
        thread.topic_keywords = extract_keywords(
            pending.message.content + " " + new_msg.content
        )

        self._threads[thread.thread_id] = thread
        self._msg_to_thread[pending.message.discord_msg_id] = thread.thread_id
        if new_msg.discord_msg_id:
            self._msg_to_thread[new_msg.discord_msg_id] = thread.thread_id

        # Remove from pending
        self._remove_pending(pending)

        self._dirty = True
        logger.debug(
            f"Promoted pending -> thread {thread.thread_id[:8]} "
            f"({pending.message.author_name} + {new_msg.author_name})"
        )
        return thread.thread_id

    def _add_pending(self, tm: ThreadMessage, channel_key: str) -> None:
        """Add a message to the pending list."""
        pm = PendingMessage(
            message=tm,
            channel_key=channel_key,
            discord_msg_id=tm.discord_msg_id,
        )
        idx = len(self._pending)
        self._pending.append(pm)
        if tm.discord_msg_id:
            self._pending_by_msg_id[tm.discord_msg_id] = idx
        self._dirty = True

    def _remove_pending(self, pending: PendingMessage) -> None:
        """Remove a pending message from the list."""
        try:
            self._pending.remove(pending)
        except ValueError:
            pass
        # Rebuild index (simple, list is small)
        self._pending_by_msg_id.clear()
        for i, pm in enumerate(self._pending):
            if pm.discord_msg_id:
                self._pending_by_msg_id[pm.discord_msg_id] = i

    # ── Thread split detection ───────────────────────────────────────

    def _check_split(self, tm: ThreadMessage, thread: Thread) -> bool:
        """Check if a message should create a child thread (topic split).

        Conservative: requires topic-shift marker + zero keyword overlap.
        """
        if not SHIFT_MARKER_PATTERN.search(tm.content):
            return False
        msg_words = set(extract_keywords(tm.content))
        thread_words = set(thread.topic_keywords)
        if not msg_words:
            return False
        return len(msg_words & thread_words) == 0

    def _create_child_thread(
        self,
        parent: Thread,
        msg: ThreadMessage,
        channel_key: str,
    ) -> str:
        """Create a child thread split from a parent."""
        child = Thread.new(channel_key)
        child.parent_thread_id = parent.thread_id
        child.add_message(msg)
        child.topic_keywords = extract_keywords(msg.content)

        parent.child_thread_ids.append(child.thread_id)

        self._threads[child.thread_id] = child
        if msg.discord_msg_id:
            self._msg_to_thread[msg.discord_msg_id] = child.thread_id

        self._dirty = True
        logger.debug(
            f"Thread split: {parent.thread_id[:8]} -> {child.thread_id[:8]} "
            f"(trigger: {msg.author_name})"
        )
        return child.thread_id

    # ── State machine ────────────────────────────────────────────────

    def tick_states(self, now: float | None = None) -> list[Thread]:
        """Advance thread state machine. Returns list of newly dead threads."""
        now = now or time.time()
        dead_threads: list[Thread] = []

        for thread in list(self._threads.values()):
            age = now - thread.updated_at

            if thread.state == ACTIVE and age > THREAD_STALE_SECONDS:
                thread.state = STALE
                self._dirty = True

            if thread.state in (STALE, RESOLVED) and age > THREAD_DEAD_SECONDS:
                thread.state = DEAD
                dead_threads.append(thread)

        # Remove dead threads from active tracking
        for dt in dead_threads:
            del self._threads[dt.thread_id]
            for msg_id in dt.discord_msg_ids:
                self._msg_to_thread.pop(msg_id, None)
            self._dirty = True

        # Expire old pending messages (> 5 min old)
        expired_pending = [
            pm for pm in self._pending if now - pm.created_at > THREAD_STALE_SECONDS
        ]
        for pm in expired_pending:
            self._remove_pending(pm)
            self._dirty = True

        # Cap active threads per channel
        self._enforce_channel_limits()

        return dead_threads

    def _enforce_channel_limits(self) -> None:
        """If a channel has too many active threads, mark oldest as stale."""
        channel_threads: dict[str, list[Thread]] = {}
        for t in self._threads.values():
            if t.state == ACTIVE:
                channel_threads.setdefault(t.channel_key, []).append(t)

        for channel_key, threads in channel_threads.items():
            if len(threads) > THREAD_MAX_ACTIVE_PER_CHANNEL:
                # Sort oldest first
                threads.sort(key=lambda t: t.updated_at)
                overflow = len(threads) - THREAD_MAX_ACTIVE_PER_CHANNEL
                for t in threads[:overflow]:
                    t.state = STALE
                    self._dirty = True

    # ── Public API (called from loop.py) ─────────────────────────────

    def ingest_batch(
        self,
        respond_msgs: list[InboundMessage],
        context_msgs: list[InboundMessage],
        channel_key: str,
    ) -> None:
        """Ingest a debounce batch of classified messages into the tracker.

        Called from _flush_debounce() after per-message classification,
        before context building.
        """
        # Tick state machine first
        self.tick_states()

        name_resolver = self._get_name_resolver()
        all_msgs: list[tuple[InboundMessage, str]] = [
            (m, "respond") for m in respond_msgs
        ] + [(m, "context") for m in context_msgs]

        for msg, classification in all_msgs:
            tm = self._wrap_message(msg, classification)

            thread_id, already_added = self._assign_message(tm, channel_key, name_resolver)

            if thread_id and thread_id in self._threads:
                thread = self._threads[thread_id]

                if already_added:
                    # Message was already added during pending promotion — skip
                    pass
                elif self._check_split(tm, thread):
                    # Check for split before adding
                    self._create_child_thread(thread, tm, channel_key)
                else:
                    thread.add_message(tm)
                    if tm.discord_msg_id:
                        self._msg_to_thread[tm.discord_msg_id] = thread_id

                    # Refresh topic keywords
                    recent_text = " ".join(
                        m.content for m in thread.messages[-10:]
                    )
                    thread.topic_keywords = extract_keywords(recent_text)

                    # Check resolution
                    if thread.check_resolution(tm):
                        thread.state = RESOLVED
                        logger.debug(
                            f"Thread {thread_id[:8]} resolved by {tm.author_name}"
                        )

                    # Reactivate stale threads that get new messages
                    if thread.state == STALE:
                        thread.state = ACTIVE

                    self._dirty = True

            # If thread_id is None, message went to pending (handled in _assign_message)

        # ── Update per-channel state for math-based classification ──
        channel_state = self.get_channel_state(channel_key)
        for msg, classification in all_msgs:
            ts = msg.timestamp.timestamp() if hasattr(msg.timestamp, "timestamp") else float(msg.timestamp)
            channel_state.update(
                sender_id=f"{msg.channel}:{msg.sender_id}",
                timestamp=ts,
                is_ene=False,
                interacted_with_ene=(classification == "respond"),
            )

    def build_context(
        self,
        respond_msgs: list[InboundMessage],
        context_msgs: list[InboundMessage],
        channel_key: str,
        format_author_fn=None,
    ) -> InboundMessage:
        """Build a multi-thread aware merged message.

        Replaces _merge_messages_tiered(). Returns an InboundMessage
        with the same metadata contract (msg_id_map, debounced, etc.).

        Side effect: updates last_shown_index on displayed threads so
        follow-up turns only show NEW messages (avoids re-replay).
        """
        result = build_threaded_context(
            threads=self._threads,
            pending=self._pending,
            respond_msgs=respond_msgs,
            context_msgs=context_msgs,
            channel_key=channel_key,
            format_author_fn=format_author_fn,
        )
        # build_threaded_context mutates last_shown_index on displayed threads
        self._dirty = True
        return result

    def mark_ene_responded(self, msg: InboundMessage) -> None:
        """Record that Ene sent a response in the relevant thread(s).

        Called from on_message() hook when Ene responds. Marks threads
        containing message IDs from this batch as ene_involved.
        """
        msg_ids = msg.metadata.get("message_ids", [])
        if not msg_ids:
            msg_id = msg.metadata.get("message_id")
            if msg_id:
                msg_ids = [msg_id]

        for mid in msg_ids:
            tid = self._msg_to_thread.get(mid)
            if tid and tid in self._threads:
                self._threads[tid].ene_involved = True
                self._dirty = True

    def archive_dead_threads(self) -> int:
        """Archive any dead threads. Returns count archived."""
        dead = self.tick_states()
        if dead:
            count = self._storage.archive_threads(dead)
            logger.info(f"Archived {count} dead threads")
            return count
        return 0

    # ── Stats ────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Get tracker statistics."""
        by_state: dict[str, int] = {}
        for t in self._threads.values():
            by_state[t.state] = by_state.get(t.state, 0) + 1
        return {
            "total_threads": len(self._threads),
            "pending_messages": len(self._pending),
            "by_state": by_state,
            "msg_index_size": len(self._msg_to_thread),
        }
