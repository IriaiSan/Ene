"""Mock channel for lab testing.

Extends BaseChannel to provide programmatic message injection and response
capture. Uses the exact same _handle_message() path as Discord/Telegram
so the agent loop processes mock messages identically to real ones.

Usage:
    mock = MockChannel(bus=bus)
    await mock.inject_message("hey ene!", sender_id="user_1")
    response = await mock.wait_for_response(timeout=30.0)
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel


@dataclass
class MockConfig:
    """Minimal config that satisfies BaseChannel expectations.

    No allow_from list by default — all senders permitted.
    """
    allow_from: list[str] = field(default_factory=list)


class MockChannel(BaseChannel):
    """Programmatic channel for lab testing.

    Injects messages via the same _handle_message() code path as real
    channels and captures outbound responses for assertion.
    """

    name = "mock"

    def __init__(self, bus: MessageBus, config: MockConfig | None = None):
        super().__init__(config or MockConfig(), bus)
        self._responses: list[OutboundMessage] = []
        self._response_event: asyncio.Event = asyncio.Event()
        self._dispatch_task: asyncio.Task | None = None

    # ── Lifecycle ─────────────────────────────────────────

    async def start(self) -> None:
        """Start the mock channel (subscribes to outbound bus)."""
        self._running = True
        self.bus.subscribe_outbound(self.name, self._on_outbound)
        logger.debug("MockChannel started")

    async def stop(self) -> None:
        """Stop the mock channel."""
        self._running = False
        logger.debug("MockChannel stopped")

    async def send(self, msg: OutboundMessage) -> None:
        """Capture an outbound message (called by bus dispatcher)."""
        self._responses.append(msg)
        self._response_event.set()

    # ── Outbound subscriber (alternative to send) ─────────

    async def _on_outbound(self, msg: OutboundMessage) -> None:
        """Subscriber callback for outbound messages on 'mock' channel."""
        self._responses.append(msg)
        self._response_event.set()

    # ── Message injection ─────────────────────────────────

    async def inject_message(
        self,
        content: str,
        sender_id: str,
        chat_id: str = "lab_general",
        *,
        display_name: str | None = None,
        username: str | None = None,
        reply_to_message_id: str | None = None,
        is_reply_to_ene: bool = False,
        guild_id: str | None = "lab_guild",
        message_id: str | None = None,
        extra_metadata: dict[str, Any] | None = None,
    ) -> None:
        """Inject a message through the standard _handle_message() path.

        Metadata fields match Discord channel output exactly so the agent
        loop processes mock messages identically to real ones.

        Args:
            content: Message text.
            sender_id: Sender platform ID.
            chat_id: Chat/channel ID (default: "lab_general").
            display_name: Sender display name (default: sender_id).
            username: Sender username (default: sender_id).
            reply_to_message_id: ID of message being replied to.
            is_reply_to_ene: Whether the reply is to an Ene message.
            guild_id: Guild/server ID (default: "lab_guild").
            message_id: Message ID (default: auto-generated UUID).
            extra_metadata: Additional metadata to merge in.
        """
        metadata: dict[str, Any] = {
            "message_id": message_id or str(uuid.uuid4()),
            "guild_id": guild_id,
            "reply_to": reply_to_message_id,
            "reply_to_author_id": None,
            "is_reply_to_ene": is_reply_to_ene,
            "author_name": display_name or sender_id,
            "username": username or sender_id,
        }
        if extra_metadata:
            metadata.update(extra_metadata)

        await self._handle_message(
            sender_id=sender_id,
            chat_id=chat_id,
            content=content,
            metadata=metadata,
        )

    # ── Response capture ──────────────────────────────────

    async def wait_for_response(self, timeout: float = 30.0) -> OutboundMessage | None:
        """Wait for the next outbound response.

        Args:
            timeout: Max seconds to wait.

        Returns:
            The OutboundMessage, or None on timeout.
        """
        # If there's already an unread response, return it
        # (track read position so multiple waits work)
        start_count = len(self._responses)

        self._response_event.clear()
        try:
            await asyncio.wait_for(self._response_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            # Check if anything arrived despite timeout race
            if len(self._responses) > start_count:
                return self._responses[start_count]
            return None

        # Return the first new response
        if len(self._responses) > start_count:
            return self._responses[start_count]
        return None

    def get_responses(self) -> list[OutboundMessage]:
        """Get all captured responses."""
        return list(self._responses)

    def get_last_response(self) -> OutboundMessage | None:
        """Get the most recent response, or None."""
        return self._responses[-1] if self._responses else None

    def clear_responses(self) -> None:
        """Clear all captured responses."""
        self._responses.clear()
        self._response_event.clear()

    @property
    def response_count(self) -> int:
        """Number of captured responses."""
        return len(self._responses)
