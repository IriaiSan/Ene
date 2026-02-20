"""Ene subsystem — modular plugin architecture for Ene's cognitive systems.

All Ene subsystems (memory, personality, goals, timeline, etc.) implement
the EneModule interface and register with the ModuleRegistry. The registry
handles tool aggregation, context injection, and lifecycle broadcasts.

Adding a new module:
1. Create a folder in nanobot/ene/ (e.g., nanobot/ene/goals/)
2. Implement EneModule in __init__.py
3. Register it in AgentLoop.__init__
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.bus.events import InboundMessage
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.base import LLMProvider
    from nanobot.session.manager import SessionManager
    from nanobot.agent.tools.base import Tool


@dataclass
class EneContext:
    """Shared context passed to all Ene modules during initialization."""

    workspace: Path
    provider: "LLMProvider"
    config: Any  # nanobot.config.schema.Config — avoid circular import
    bus: "MessageBus"
    sessions: "SessionManager"


class EneModule(ABC):
    """Base class for all Ene subsystem modules.

    Lifecycle:
        1. initialize(ctx) — called once on startup
        2. get_tools() — tools registered with the agent
        3. get_context_block() — static text injected into every system prompt
        4. get_context_block_for_message(msg) — dynamic text per message
        5. on_message(msg, responded) — called after each message
        6. on_idle(seconds) — called when conversation goes idle
        7. on_daily() — called on daily maintenance schedule
        8. shutdown() — cleanup on shutdown
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique module name (e.g., 'memory', 'personality', 'goals')."""
        ...

    @abstractmethod
    async def initialize(self, ctx: EneContext) -> None:
        """Called once on startup. Set up resources, run migrations, etc."""
        ...

    @abstractmethod
    def get_tools(self) -> list["Tool"]:
        """Return tools this module provides to the agent."""
        ...

    def get_context_block(self) -> str | None:
        """Return static text to inject into every system prompt.

        Returns None to skip. Called once per system prompt build.
        """
        return None

    def set_sender_context(self, platform_id: str, metadata: dict) -> None:
        """Optional: receive current sender info before context building.

        Called before get_context_block_for_message() so modules can
        look up the speaker and prepare per-message context.
        Default is a no-op.
        """

    def get_context_block_for_message(self, message: str) -> str | None:
        """Return dynamic context based on the current user message.

        Used for retrieval-augmented context (e.g., searching memories
        relevant to the current conversation). Returns None to skip.
        """
        return None

    async def on_message(self, msg: "InboundMessage", responded: bool) -> None:
        """Hook called after every inbound message (lurked or responded).

        Args:
            msg: The inbound message.
            responded: True if Ene responded, False if she lurked.
        """

    async def on_idle(self, idle_seconds: float) -> None:
        """Hook called when conversation goes idle.

        Args:
            idle_seconds: Seconds since the last inbound message.
        """

    async def on_daily(self) -> None:
        """Hook called on the daily maintenance schedule (e.g., 4 AM)."""

    async def shutdown(self) -> None:
        """Cleanup on shutdown. Close connections, flush buffers, etc."""


class ModuleRegistry:
    """Discovers and manages Ene modules.

    The AgentLoop creates a ModuleRegistry and registers modules.
    It then uses the registry to aggregate tools, context, and
    broadcast lifecycle events.
    """

    def __init__(self) -> None:
        self._modules: dict[str, EneModule] = {}
        self._current_sender_id: str = ""
        self._current_channel: str = ""
        self._current_metadata: dict = {}
        self._scene_participant_ids: list[str] | None = None  # Set by loop.py per batch

    def set_current_sender(
        self, sender_id: str, channel: str, metadata: dict
    ) -> None:
        """Set the current sender for context injection.

        Called before build_messages() so modules know who is speaking.
        """
        self._current_sender_id = sender_id
        self._current_channel = channel
        self._current_metadata = metadata

    def get_current_platform_id(self) -> str:
        """Get current sender's platform ID (e.g., 'discord:123456')."""
        if self._current_sender_id:
            return f"{self._current_channel}:{self._current_sender_id}"
        return ""

    def get_current_metadata(self) -> dict:
        """Get current sender's metadata."""
        return self._current_metadata

    def set_scene_participants(self, participant_ids: list[str]) -> None:
        """Set participant IDs for Scene Brief generation.

        Called from loop.py after ingest_batch(). When set, the social
        module's get_scene_context() replaces the single-person card
        in get_all_dynamic_context().
        """
        self._scene_participant_ids = participant_ids

    def clear_scene_participants(self) -> None:
        """Clear scene participants after batch processing."""
        self._scene_participant_ids = None

    def get_module(self, name: str) -> "EneModule | None":
        """Get a module by name (alias for get)."""
        return self._modules.get(name)

    def register(self, module: EneModule) -> None:
        """Register an Ene module."""
        if module.name in self._modules:
            logger.warning(f"Module '{module.name}' already registered, replacing")
        self._modules[module.name] = module
        logger.info(f"Registered Ene module: {module.name}")

    def get(self, name: str) -> EneModule | None:
        """Get a module by name."""
        return self._modules.get(name)

    @property
    def modules(self) -> dict[str, EneModule]:
        """All registered modules."""
        return dict(self._modules)

    async def initialize_all(self, ctx: EneContext) -> None:
        """Initialize all registered modules."""
        for name, module in self._modules.items():
            try:
                await module.initialize(ctx)
                logger.info(f"Initialized module: {name}")
            except Exception as e:
                logger.error(f"Failed to initialize module '{name}': {e}", exc_info=True)

    def get_all_tools(self) -> list["Tool"]:
        """Aggregate tools from all modules."""
        tools: list["Tool"] = []
        for module in self._modules.values():
            try:
                tools.extend(module.get_tools())
            except Exception as e:
                logger.error(f"Error getting tools from '{module.name}': {e}")
        return tools

    def get_all_context_blocks(self) -> str:
        """Aggregate static context blocks from all modules.

        Returns a single string with all blocks joined by newlines.
        """
        blocks: list[str] = []
        for module in self._modules.values():
            try:
                block = module.get_context_block()
                if block:
                    blocks.append(block)
            except Exception as e:
                logger.error(f"Error getting context from '{module.name}': {e}")
        return "\n\n".join(blocks)

    def get_all_dynamic_context(self, message: str) -> str:
        """Aggregate dynamic context from all modules for a given message.

        First tells modules who is speaking (via set_sender_context),
        then collects dynamic context blocks. If scene participants are
        set (multi-person batch), uses get_scene_context() on the social
        module instead of the single-person card.
        Returns a single string with all dynamic blocks joined by newlines.
        """
        # Tell modules who is speaking before asking for context
        platform_id = self.get_current_platform_id()
        metadata = self.get_current_metadata()
        if platform_id:
            for module in self._modules.values():
                try:
                    module.set_sender_context(platform_id, metadata)
                except Exception as e:
                    logger.error(
                        f"Error setting sender context on '{module.name}': {e}"
                    )

        blocks: list[str] = []

        # If scene participants are set, use scene context from social module
        # instead of the per-module loop for the social module's card
        scene_used = False
        if self._scene_participant_ids is not None:
            social_mod = self._modules.get("social")
            if social_mod and hasattr(social_mod, "get_scene_context"):
                try:
                    scene_block = social_mod.get_scene_context(
                        primary_id=platform_id,
                        participant_ids=self._scene_participant_ids,
                    )
                    if scene_block:
                        blocks.append(scene_block)
                        scene_used = True
                except Exception as e:
                    logger.error(f"Error getting scene context from social: {e}")

        for module in self._modules.values():
            try:
                # Skip social module's per-message card if scene context was used
                if scene_used and module.name == "social":
                    continue
                block = module.get_context_block_for_message(message)
                if block:
                    blocks.append(block)
            except Exception as e:
                logger.error(f"Error getting dynamic context from '{module.name}': {e}")
        return "\n\n".join(blocks)

    async def notify_message(
        self, msg: "InboundMessage", responded: bool
    ) -> None:
        """Broadcast message event to all modules."""
        for module in self._modules.values():
            try:
                await module.on_message(msg, responded)
            except Exception as e:
                logger.error(f"Error in '{module.name}'.on_message: {e}")

    async def notify_idle(self, idle_seconds: float) -> None:
        """Broadcast idle event to all modules."""
        for module in self._modules.values():
            try:
                await module.on_idle(idle_seconds)
            except Exception as e:
                logger.error(f"Error in '{module.name}'.on_idle: {e}")

    async def notify_daily(self) -> None:
        """Broadcast daily maintenance event to all modules."""
        for module in self._modules.values():
            try:
                await module.on_daily()
            except Exception as e:
                logger.error(f"Error in '{module.name}'.on_daily: {e}")

    async def shutdown_all(self) -> None:
        """Shutdown all modules."""
        for name, module in self._modules.items():
            try:
                await module.shutdown()
                logger.info(f"Shutdown module: {name}")
            except Exception as e:
                logger.error(f"Error shutting down '{name}': {e}")
