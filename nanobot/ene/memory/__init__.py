"""Ene Memory Module — Module 1 of the Ene subsystem architecture.

Implements four-layer memory with editable core, vector search,
entity tracking, sleep-time processing, and importance-based decay.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.ene import EneModule, EneContext

if TYPE_CHECKING:
    from nanobot.agent.tools.base import Tool
    from nanobot.bus.events import InboundMessage


class MemoryModule(EneModule):
    """Memory module for Ene — manages core memory, vector memory, and sleep agent.

    Lifecycle:
        1. initialize() — creates MemorySystem, runs migration, sets up sleep agent
        2. get_tools() — returns save/edit/delete/search memory tools
        3. get_context_block() — returns core memory + diary for system prompt
        4. get_context_block_for_message(msg) — retrieves relevant memories + entities
        5. on_idle(seconds) — triggers sleep agent quick processing
        6. on_daily() — triggers sleep agent deep processing
    """

    def __init__(
        self,
        token_budget: int = 4000,
        chroma_path: str | None = None,
        embedding_model: str = "openai/text-embedding-3-small",
        idle_trigger_seconds: int = 300,
        diary_context_days: int = 3,
    ):
        self._token_budget = token_budget
        self._chroma_path = chroma_path
        self._embedding_model = embedding_model
        self._idle_trigger_seconds = idle_trigger_seconds
        self._diary_context_days = diary_context_days
        self._system: Any = None  # MemorySystem, set in initialize()
        self._sleep_agent: Any = None  # SleepTimeAgent, set in initialize()
        self._ctx: EneContext | None = None
        self._idle_processed = False  # Track if idle was already processed this cycle

    @property
    def name(self) -> str:
        return "memory"

    async def initialize(self, ctx: EneContext) -> None:
        """Initialize the memory system."""
        from nanobot.ene.memory.system import MemorySystem
        from nanobot.ene.memory.embeddings import EneEmbeddings

        self._ctx = ctx

        # Set up embedding function
        embedding_fn = None
        try:
            api_key = ctx.config.get_api_key() if hasattr(ctx.config, 'get_api_key') else None
            api_base = ctx.config.get_api_base() if hasattr(ctx.config, 'get_api_base') else None
            embedder = EneEmbeddings(
                model=self._embedding_model,
                api_key=api_key,
                api_base=api_base,
            )
            embedding_fn = embedder.embed
            logger.info(f"Embedding model: {self._embedding_model}")
        except Exception as e:
            logger.warning(f"Failed to set up embeddings, using ChromaDB default: {e}")

        # Initialize MemorySystem
        chroma_path = self._chroma_path or str(ctx.workspace / "chroma_db")
        self._system = MemorySystem(
            workspace=ctx.workspace,
            token_budget=self._token_budget,
            chroma_path=chroma_path,
            embedding_fn=embedding_fn,
            diary_context_days=self._diary_context_days,
        )
        self._system.initialize()

        logger.info(
            f"Memory module initialized: "
            f"core={self._system.core.get_total_tokens()}/{self._token_budget} tokens, "
            f"vector={'OK' if self._system.vector else 'unavailable'}"
        )

    def get_tools(self) -> list["Tool"]:
        """Return memory tools (save, edit, delete, search)."""
        if self._system is None:
            return []

        from nanobot.ene.memory.tools import (
            SaveMemoryTool,
            EditMemoryTool,
            DeleteMemoryTool,
            SearchMemoryTool,
        )

        return [
            SaveMemoryTool(self._system),
            EditMemoryTool(self._system),
            DeleteMemoryTool(self._system),
            SearchMemoryTool(self._system),
        ]

    def get_context_block(self) -> str | None:
        """Return core memory + diary for system prompt injection."""
        if self._system is None:
            return None
        return self._system.get_memory_context()

    def get_context_block_for_message(self, message: str) -> str | None:
        """Return retrieval-augmented context for the current message."""
        if self._system is None:
            return None
        context = self._system.get_relevant_context(message)
        return context if context else None

    async def on_message(self, msg: "InboundMessage", responded: bool) -> None:
        """Reset idle processing flag on each message."""
        self._idle_processed = False

    async def on_idle(self, idle_seconds: float) -> None:
        """Trigger sleep agent quick processing after idle threshold."""
        if idle_seconds < self._idle_trigger_seconds:
            return
        if self._idle_processed:
            return
        self._idle_processed = True

        if self._sleep_agent:
            try:
                await self._sleep_agent.process_idle()
            except Exception as e:
                logger.error(f"Sleep agent idle processing failed: {e}")

    async def on_daily(self) -> None:
        """Trigger sleep agent deep processing."""
        if self._sleep_agent:
            try:
                await self._sleep_agent.process_daily()
            except Exception as e:
                logger.error(f"Sleep agent daily processing failed: {e}")

    async def shutdown(self) -> None:
        """Cleanup on shutdown."""
        logger.info("Memory module shutdown")

    @property
    def system(self) -> Any:
        """Access the MemorySystem facade (for testing/external use)."""
        return self._system
