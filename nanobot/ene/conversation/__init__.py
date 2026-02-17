"""Conversation Tracker Module (Module 5) — thread detection and tracking.

Detects, tracks, and presents conversation threads to Ene so she has
multi-thread awareness in group chats. Instead of seeing a flat wall of
interleaved messages, Ene sees distinct conversation threads with
participants, history, and state.

Architecture:
    - ConversationTracker (tracker.py): core engine, assigns messages to threads
    - ThreadMessage/Thread (models.py): data structures with state machine
    - Signal scoring (signals.py): heuristic thread detection (no LLM)
    - Formatter (formatter.py): multi-thread context builder
    - ThreadStorage (storage.py): disk persistence

Hooks into the pipeline:
    - _flush_debounce: ingest_batch() + build_context() replace flat merge
    - on_message: records Ene's responses in threads
    - on_idle: tick state machine + periodic save
    - on_daily: archive dead threads
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

from nanobot.ene import EneModule, EneContext

if TYPE_CHECKING:
    from nanobot.agent.tools.base import Tool
    from nanobot.bus.events import InboundMessage

from .tracker import ConversationTracker


class ConversationTrackerModule(EneModule):
    """Conversation tracking module — detects and maintains thread structure.

    Provides:
    - Thread detection and assignment for incoming messages
    - Multi-thread aware context formatting for the LLM
    - Thread lifecycle management (active → stale → dead)
    - Disk persistence for thread state across restarts
    """

    def __init__(self) -> None:
        self.tracker: ConversationTracker | None = None
        self._ctx: EneContext | None = None
        self._save_cooldown = 60.0  # Save at most every 60s
        self._last_save = 0.0

    @property
    def name(self) -> str:
        return "conversation_tracker"

    async def initialize(self, ctx: EneContext) -> None:
        """Create tracker and load persisted state."""
        self._ctx = ctx
        thread_dir = ctx.workspace / "memory" / "threads"

        self.tracker = ConversationTracker(
            thread_dir=thread_dir,
            social_registry=None,  # Wired up after all modules init
        )
        self.tracker.load_state()
        logger.info("Conversation Tracker module initialized")

    def get_tools(self) -> list["Tool"]:
        """No tools in Phase 1. Future: ReadThreadTool."""
        return []

    def get_context_block(self) -> str | None:
        """Thread awareness instructions for the system prompt."""
        return (
            "## Conversation Awareness\n"
            "Messages are shown grouped by conversation thread.\n"
            "Each thread has its own participants and topic.\n"
            "When replying, use reply_to with the #msgN tag of the specific "
            "message you want to reply to — this ensures your response threads "
            "correctly in Discord.\n"
            "If multiple threads need your attention, you can send multiple "
            "messages using the message tool — one per thread.\n"
            "Background threads are conversations you're not part of — "
            "just awareness of what's happening around you.\n"
            "Stale threads are winding down. Resolved threads got their answer."
        )

    async def on_message(self, msg: "InboundMessage", responded: bool) -> None:
        """Record that Ene responded in the relevant thread."""
        if responded and self.tracker:
            self.tracker.mark_ene_responded(msg)

    async def on_idle(self, idle_seconds: float) -> None:
        """Tick state machine and save periodically."""
        if not self.tracker:
            return

        # Always tick states
        dead = self.tracker.tick_states()
        if dead:
            from .storage import ThreadStorage

            storage = self.tracker._storage
            storage.archive_threads(dead)

        # Save at most every 60s
        import time

        now = time.time()
        if now - self._last_save >= self._save_cooldown:
            self.tracker.save_if_dirty()
            self._last_save = now

    async def on_daily(self) -> None:
        """Archive dead threads, clean old archives, save state."""
        if not self.tracker:
            return

        archived = self.tracker.archive_dead_threads()
        if archived:
            logger.info(f"Daily: archived {archived} dead threads")

        # Clean archives older than 30 days
        self.tracker._storage.delete_old_archives(keep_days=30)
        self.tracker.save_state()

    async def shutdown(self) -> None:
        """Save state on shutdown."""
        if self.tracker:
            self.tracker.save_state()
            logger.info("Conversation Tracker: state saved on shutdown")
