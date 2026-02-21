"""Public state accessors and control panel methods.

Extracted from AgentLoop (Phase 6 refactor).
Groups all introspection and manual control methods:
    - hard_reset: wipe all pipeline state for a clean slate
    - Brain toggle: pause/resume LLM response generation
    - Model switching: hot-swap the primary LLM model
    - Security accessors: mute/unmute, rate limit, jailbreak scores

All state lives on AgentLoop — StateInspector accesses it through
a back-reference (self._loop).
"""

from __future__ import annotations

import time as _time
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


class StateInspector:
    """Public state accessors and manual control methods."""

    def __init__(self, loop: "AgentLoop") -> None:
        self._loop = loop

    # ── Hard reset ────────────────────────────────────────────────────

    def hard_reset(self) -> None:
        """Drop all queued messages and reset pipeline state for a clean debug slate.

        Clears:
        - All debounce intake buffers (messages waiting to be batched)
        - All channel queues (batches waiting to be processed)
        - All debounce timers (cancels pending flush tasks)
        - All queue processor tasks (cancels in-flight batch processing)
        - Session cache (next message will reload from disk, avoiding context poisoning)

        Does NOT clear: session files on disk, memory, social graph, or any module state.
        The agent loop continues running — next arriving message starts fresh.
        """
        loop = self._loop

        # Cancel all pending debounce timers
        for task in list(loop._debounce_timers.values()):
            if not task.done():
                task.cancel()
        loop._debounce_timers.clear()

        # Cancel all queue processor tasks
        for task in list(loop._queue_processors.values()):
            if not task.done():
                task.cancel()
        loop._queue_processors.clear()

        # Drop all buffered and queued messages
        dropped_msgs = sum(len(b) for b in loop._debounce_buffers.values())
        dropped_batches = sum(len(q) for q in loop._channel_queues.values())
        loop._debounce_buffers.clear()
        loop._channel_queues.clear()

        # Clear all session history (in-memory AND on-disk)
        for session in loop.sessions._cache.values():
            session.clear()
            loop.sessions.save(session)
        loop.sessions._cache.clear()

        # Clear session summaries and counters
        loop._session_summaries.clear()
        loop._summary_msg_counters.clear()

        # Clear conversation tracker state (threads + pending)
        conv_mod = loop.module_registry.get_module("conversation_tracker")
        if conv_mod and hasattr(conv_mod, "tracker") and conv_mod.tracker:
            tracker = conv_mod.tracker
            tracker._threads.clear()
            tracker._pending.clear()
            tracker._pending_by_msg_id.clear()
            tracker._msg_to_thread.clear()
            tracker._dirty = True
            tracker.save_state()
            logger.info("Hard reset: cleared all threads and pending messages")

        logger.warning(
            f"Hard reset: dropped {dropped_msgs} buffered msgs, "
            f"{dropped_batches} queued batches. Sessions + threads wiped."
        )

        # Update live state panel
        loop._live.update_state(
            buffers={},
            queues={},
            processing=None,
            muted_count=0,
            active_batch=None,
        )

    # ── Model switching ───────────────────────────────────────────────

    def set_model(self, model: str) -> str:
        """Hot-swap the primary LLM model. Returns previous model name."""
        loop = self._loop
        old = loop.model
        loop.model = model
        loop.provider.set_primary_model(model)
        logger.info(f"Model switched: {old} → {model}")
        loop._live.emit("model_switch", "", old_model=old, new_model=model)
        return old

    def get_model(self) -> str:
        """Get the current primary model name."""
        return self._loop.model

    # ── Brain toggle ──────────────────────────────────────────────────

    def pause_brain(self) -> None:
        """Pause LLM responses. Discord stays connected, dashboard works, messages observed."""
        self._loop._brain_enabled = False
        logger.info("Brain paused — messages observed, no LLM calls")
        self._loop._live.emit("brain_status_changed", "", status="paused")

    def resume_brain(self) -> None:
        """Resume LLM responses."""
        self._loop._brain_enabled = True
        logger.info("Brain resumed — LLM responses active")
        self._loop._live.emit("brain_status_changed", "", status="resumed")

    def is_brain_enabled(self) -> bool:
        """Check if the brain (LLM response generation) is enabled."""
        return self._loop._brain_enabled

    # ── Security state accessors ──────────────────────────────────────

    def get_muted_users(self) -> dict[str, float]:
        """Return muted users dict: caller_id → expiry timestamp."""
        now = _time.time()
        return {uid: exp for uid, exp in self._loop._muted_users.items() if exp > now}

    def get_rate_limit_state(self) -> dict[str, list[float]]:
        """Return rate limit timestamps per user."""
        return dict(self._loop._user_message_timestamps)

    def get_jailbreak_scores(self) -> dict[str, list[float]]:
        """Return jailbreak detection scores per user."""
        return dict(self._loop._user_jailbreak_scores)

    def mute_user(self, caller_id: str, duration_min: float = 30.0) -> None:
        """Manually mute a user for the specified duration."""
        self._loop._muted_users[caller_id] = _time.time() + (duration_min * 60)
        logger.info(f"Manual mute: {caller_id} for {duration_min}min")
        self._loop._live.emit("mute_event", "", sender=caller_id, duration_min=duration_min, reason="manual_dashboard")

    def unmute_user(self, caller_id: str) -> bool:
        """Unmute a user. Returns True if they were muted."""
        if caller_id in self._loop._muted_users:
            del self._loop._muted_users[caller_id]
            logger.info(f"Manual unmute: {caller_id}")
            return True
        return False

    def clear_rate_limit(self, caller_id: str) -> bool:
        """Clear rate limit timestamps for a user."""
        if caller_id in self._loop._user_message_timestamps:
            del self._loop._user_message_timestamps[caller_id]
            return True
        return False
