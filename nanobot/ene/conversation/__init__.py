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
        self._focus_target: dict | None = None  # Phase 2.2: per-thread focus directive

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
            "## How You See Conversations\n"
            "You see everything happening in the channel — all threads, all people.\n"
            "You are responding to one specific thread right now.\n"
            "The Scene section shows who is around. The Focus section tells you "
            "who you're talking to.\n"
            "Background threads are your peripheral vision. You don't respond to them "
            "directly, but you're aware they exist and can reference them if it feels "
            "natural.\n"
            "You send ONE message. Use reply_to with the #msgN tag of the message "
            "you're responding to."
        )

    def set_focus_target(self, name: str, topic: str | None = None) -> None:
        """Set the per-thread focus target for the next LLM call.

        Called from loop.py before each per-thread _run_agent_loop().
        The focus directive tells the LLM exactly who to respond to.
        """
        self._focus_target = {"name": name, "topic": topic or "their message"}

    def clear_focus_target(self) -> None:
        """Clear the focus target after a per-thread LLM call completes."""
        self._focus_target = None

    def get_context_block_for_message(self, message: str) -> str | None:
        """Return per-thread Focus directive if a focus target is set."""
        if not self._focus_target:
            return None
        return (
            "## Focus\n"
            f"Your primary target is **{self._focus_target['name']}** "
            f"in the thread about {self._focus_target.get('topic', 'their message')}.\n"
            "Respond to them. One message."
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
