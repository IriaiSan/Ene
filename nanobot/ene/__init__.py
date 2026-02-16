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

        Returns a single string with all dynamic blocks joined by newlines.
        """
        blocks: list[str] = []
        for module in self._modules.values():
            try:
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
