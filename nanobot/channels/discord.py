"""Discord channel implementation using Discord Gateway websocket."""

import asyncio
import json
import re
from pathlib import Path
from typing import Any

import httpx
import websockets
from loguru import logger

from nanobot.bus.events import OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.channels.base import BaseChannel
from nanobot.config.schema import DiscordConfig


DISCORD_API_BASE = "https://discord.com/api/v10"
MAX_ATTACHMENT_BYTES = 20 * 1024 * 1024  # 20MB

# Word-boundary match to avoid false positives ("generic", "scene", etc.)
_ENE_PATTERN = re.compile(r"\bene\b", re.IGNORECASE)

# Ene: only respond in these guilds (servers). Empty = all guilds allowed.
# If someone adds Ene to another server, she'll ignore all messages there.
ALLOWED_GUILD_IDS: set[str] = {
    "1306235136400035911",  # Dad's server
}
# DMs have guild_id=None, which we allow separately via the DM gate in loop.py


class DiscordChannel(BaseChannel):
    """Discord channel using Gateway websocket."""

    name = "discord"

    def __init__(self, config: DiscordConfig, bus: MessageBus):
        super().__init__(config, bus)
        self.config: DiscordConfig = config
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._seq: int | None = None
        self._session_id: str | None = None  # For RESUME
        self._resume_url: str | None = None  # Gateway URL for resume
        self._heartbeat_task: asyncio.Task | None = None
        self._typing_tasks: dict[str, asyncio.Task] = {}
        self._http: httpx.AsyncClient | None = None
        self._bot_user_id: str | None = None  # Ene: own Discord user ID (for @mention detection)
        self._consecutive_failures: int = 0  # For exponential backoff

    async def start(self) -> None:
        """Start the Discord gateway connection."""
        if not self.config.token:
            logger.error("Discord bot token not configured")
            return

        self._running = True
        self._http = httpx.AsyncClient(timeout=30.0)

        while self._running:
            try:
                # Use resume URL if available, otherwise default gateway
                url = self._resume_url or self.config.gateway_url
                logger.info(f"Connecting to Discord gateway...")
                async with websockets.connect(url) as ws:
                    self._ws = ws
                    self._consecutive_failures = 0  # Reset on successful connect
                    await self._gateway_loop()
            except asyncio.CancelledError:
                break
            except Exception as e:
                self._consecutive_failures += 1
                # Exponential backoff: 5s, 10s, 20s, 40s, 60s max
                delay = min(5 * (2 ** (self._consecutive_failures - 1)), 60)
                logger.warning(f"Discord gateway error: {e}")
                if self._running:
                    logger.info(
                        f"Reconnecting in {delay}s "
                        f"(attempt {self._consecutive_failures})..."
                    )
                    await asyncio.sleep(delay)

    async def stop(self) -> None:
        """Stop the Discord channel."""
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        for task in self._typing_tasks.values():
            task.cancel()
        self._typing_tasks.clear()
        if self._ws:
            await self._ws.close()
            self._ws = None
        if self._http:
            await self._http.aclose()
            self._http = None

    async def send(self, msg: OutboundMessage) -> None:
        """Send a message through Discord REST API.

        Messages over 2000 chars are split on paragraph/sentence boundaries
        and sent as consecutive messages. Only the first chunk uses reply_to.
        """
        if not self._http:
            logger.warning("Discord HTTP client not initialized")
            return

        chunks = self._chunk_message(msg.content)

        try:
            for i, chunk in enumerate(chunks):
                url = f"{DISCORD_API_BASE}/channels/{msg.chat_id}/messages"
                payload: dict[str, Any] = {"content": chunk}

                # Only the first chunk is a reply
                if i == 0 and msg.reply_to:
                    payload["message_reference"] = {"message_id": msg.reply_to}
                    payload["allowed_mentions"] = {"replied_user": False}

                headers = {"Authorization": f"Bot {self.config.token}"}

                for attempt in range(3):
                    try:
                        response = await self._http.post(url, headers=headers, json=payload)
                        if response.status_code == 429:
                            data = response.json()
                            retry_after = float(data.get("retry_after", 1.0))
                            logger.warning(f"Discord rate limited, retrying in {retry_after}s")
                            await asyncio.sleep(retry_after)
                            continue
                        response.raise_for_status()
                        break
                    except Exception as e:
                        if attempt == 2:
                            logger.error(f"Error sending Discord message (chunk {i+1}/{len(chunks)}): {e}")
                        else:
                            await asyncio.sleep(1)
        finally:
            await self._stop_typing(msg.chat_id)

    @staticmethod
    def _chunk_message(content: str, limit: int = 2000) -> list[str]:
        """Split a message into chunks that fit Discord's character limit.

        Splits on paragraph boundaries first, then sentence boundaries,
        then hard-cuts as a last resort. Never breaks mid-word if avoidable.
        """
        if len(content) <= limit:
            return [content]

        chunks: list[str] = []
        remaining = content

        while remaining:
            if len(remaining) <= limit:
                chunks.append(remaining)
                break

            # Try to split at a paragraph boundary (\n\n)
            cut = remaining.rfind("\n\n", 0, limit)
            if cut > limit // 4:
                chunks.append(remaining[:cut].rstrip())
                remaining = remaining[cut:].lstrip("\n")
                continue

            # Try single newline
            cut = remaining.rfind("\n", 0, limit)
            if cut > limit // 4:
                chunks.append(remaining[:cut].rstrip())
                remaining = remaining[cut:].lstrip("\n")
                continue

            # Try sentence boundary
            cut = remaining.rfind(". ", 0, limit)
            if cut > limit // 4:
                cut += 1  # Include the period
                chunks.append(remaining[:cut].rstrip())
                remaining = remaining[cut:].lstrip()
                continue

            # Try space (word boundary)
            cut = remaining.rfind(" ", 0, limit)
            if cut > limit // 4:
                chunks.append(remaining[:cut].rstrip())
                remaining = remaining[cut:].lstrip()
                continue

            # Hard cut — no good boundary found
            chunks.append(remaining[:limit])
            remaining = remaining[limit:]

        return [c for c in chunks if c.strip()]

    async def _gateway_loop(self) -> None:
        """Main gateway loop: identify, heartbeat, dispatch events."""
        if not self._ws:
            return

        async for raw in self._ws:
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(f"Invalid JSON from Discord gateway: {raw[:100]}")
                continue

            op = data.get("op")
            event_type = data.get("t")
            seq = data.get("s")
            payload = data.get("d")

            if seq is not None:
                self._seq = seq

            # DEBUG: log all gateway events so we can see what Discord sends
            if op == 0 and event_type not in ("READY", "GUILD_CREATE"):
                logger.debug(f"Discord gateway event: op={op} t={event_type} seq={seq}")

            if op == 10:
                # HELLO: start heartbeat, then identify or resume
                interval_ms = payload.get("heartbeat_interval", 45000)
                await self._start_heartbeat(interval_ms / 1000)
                if self._session_id and self._seq is not None:
                    await self._resume()
                else:
                    await self._identify()
            elif op == 0 and event_type == "READY":
                # Capture session info for RESUME on reconnect
                self._session_id = payload.get("session_id")
                self._resume_url = payload.get("resume_gateway_url")
                self._bot_user_id = payload.get("user", {}).get("id")
                logger.info(f"Discord gateway READY (bot user ID: {self._bot_user_id})")
            elif op == 0 and event_type == "RESUMED":
                logger.info("Discord gateway RESUMED successfully")
            elif op == 0 and event_type == "MESSAGE_CREATE":
                logger.debug(f"Discord MESSAGE_CREATE from {payload.get('author', {}).get('username', '?')} in channel {payload.get('channel_id', '?')}")
                await self._handle_message_create(payload)
            elif op == 7:
                # RECONNECT: exit loop to reconnect (keep session for RESUME)
                logger.info("Discord gateway requested reconnect")
                break
            elif op == 9:
                # INVALID_SESSION: d=True means resumable, d=False means not
                resumable = payload if isinstance(payload, bool) else False
                if not resumable:
                    # Session is dead — clear it so next connect does fresh IDENTIFY
                    logger.warning("Discord gateway invalid session (not resumable)")
                    self._session_id = None
                    self._resume_url = None
                    self._seq = None
                else:
                    logger.info("Discord gateway invalid session (resumable)")
                break
            elif op == 11:
                # HEARTBEAT_ACK — normal, just track it
                pass
            elif op == 1:
                # HEARTBEAT request from server
                heartbeat = {"op": 1, "d": self._seq}
                await self._ws.send(json.dumps(heartbeat))
            else:
                if op != 0:
                    logger.debug(f"Discord gateway unknown op={op} t={event_type}")

    async def _identify(self) -> None:
        """Send IDENTIFY payload."""
        if not self._ws:
            return

        identify = {
            "op": 2,
            "d": {
                "token": self.config.token,
                "intents": self.config.intents,
                "properties": {
                    "os": "nanobot",
                    "browser": "nanobot",
                    "device": "nanobot",
                },
            },
        }
        logger.info(f"Discord IDENTIFY sent with intents={self.config.intents} (binary: {bin(self.config.intents)})")
        await self._ws.send(json.dumps(identify))

    async def _resume(self) -> None:
        """Send RESUME payload to reconnect without losing events."""
        if not self._ws:
            return

        resume = {
            "op": 6,
            "d": {
                "token": self.config.token,
                "session_id": self._session_id,
                "seq": self._seq,
            },
        }
        logger.info(f"Discord RESUME sent (session={self._session_id}, seq={self._seq})")
        await self._ws.send(json.dumps(resume))

    async def _start_heartbeat(self, interval_s: float) -> None:
        """Start or restart the heartbeat loop."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()

        async def heartbeat_loop() -> None:
            while self._running and self._ws:
                payload = {"op": 1, "d": self._seq}
                try:
                    await self._ws.send(json.dumps(payload))
                except Exception as e:
                    logger.warning(f"Discord heartbeat failed: {e}")
                    break
                await asyncio.sleep(interval_s)

        self._heartbeat_task = asyncio.create_task(heartbeat_loop())

    async def _handle_message_create(self, payload: dict[str, Any]) -> None:
        """Handle incoming Discord messages."""
        author = payload.get("author") or {}
        if author.get("bot"):
            logger.debug(f"Discord: ignoring bot message from {author.get('username', '?')}")
            return

        sender_id = str(author.get("id", ""))
        channel_id = str(payload.get("channel_id", ""))
        content = payload.get("content") or ""
        guild_id = payload.get("guild_id")

        logger.debug(f"Discord msg: sender={sender_id} channel={channel_id} guild={guild_id} content={content[:80]!r}")

        if not sender_id or not channel_id:
            logger.debug("Discord: dropping — missing sender_id or channel_id")
            return

        # Ene: guild whitelist — ignore messages from unauthorized servers
        # DMs have guild_id=None, which is allowed (filtered by DM gate in loop.py)
        if ALLOWED_GUILD_IDS and guild_id is not None and str(guild_id) not in ALLOWED_GUILD_IDS:
            logger.debug(f"Discord: dropping — guild {guild_id} not in whitelist")
            return

        if not self.is_allowed(sender_id):
            logger.debug(f"Discord: dropping — sender {sender_id} not in allowFrom")
            return

        logger.info(f"Discord: message from {author.get('username', '?')} passed all filters, forwarding to bus")

        # Ene: capture display name + username for identity
        # display_name = server nickname (changes) or global name
        # username = stable Discord username (e.g., "ash_vi0") — rarely changes
        display_name = (payload.get("member") or {}).get("nick") or author.get("global_name") or author.get("username") or sender_id
        username = author.get("username") or ""

        # Ene: resolve own @mention to "ene" so _should_respond picks it up
        if self._bot_user_id and f"<@{self._bot_user_id}>" in content:
            content = content.replace(f"<@{self._bot_user_id}>", "@ene")

        content_parts = [content] if content else []
        media_paths: list[str] = []

        # Ene: handle attachments — images become text descriptions since DeepSeek has no vision
        for attachment in payload.get("attachments") or []:
            filename = attachment.get("filename") or "attachment"
            content_type = attachment.get("content_type") or ""

            if content_type.startswith("image/"):
                # Don't download images — model can't see them
                content_parts.append(f"[{display_name} sent an image: {filename}]")
                logger.debug(f"Skipped image attachment from {display_name}: {filename}")
            else:
                # Non-image attachments: note them but don't download
                content_parts.append(f"[{display_name} sent a file: {filename}]")

        referenced_message = payload.get("referenced_message") or {}
        reply_to = referenced_message.get("id")
        # Ene: extract who was replied to — if it's Ene, she should respond
        reply_to_author_id = (referenced_message.get("author") or {}).get("id")
        is_reply_to_ene = reply_to_author_id == self._bot_user_id if (reply_to_author_id and self._bot_user_id) else False

        # Ene: only start typing if she's likely to respond
        # (mentioned by name, @mention, reply to Ene, or it's a DM). Avoids infinite
        # "Ene is typing..." on lurked public messages.
        is_dm = guild_id is None
        might_respond = is_dm or bool(_ENE_PATTERN.search(content)) or is_reply_to_ene
        if might_respond:
            await self._start_typing(channel_id)

        await self._handle_message(
            sender_id=sender_id,
            chat_id=channel_id,
            content="\n".join(p for p in content_parts if p) or "[empty message]",
            media=media_paths,
            metadata={
                "message_id": str(payload.get("id", "")),
                "guild_id": payload.get("guild_id"),
                "reply_to": reply_to,
                "reply_to_author_id": reply_to_author_id,
                "is_reply_to_ene": is_reply_to_ene,
                "author_name": display_name,
                "username": username,
            },
        )

    async def _start_typing(self, channel_id: str) -> None:
        """Start periodic typing indicator for a channel.

        Auto-expires after 30 seconds to prevent infinite typing on lurked
        messages (where Ene never sends a response to clear the indicator).
        """
        await self._stop_typing(channel_id)

        async def typing_loop() -> None:
            url = f"{DISCORD_API_BASE}/channels/{channel_id}/typing"
            headers = {"Authorization": f"Bot {self.config.token}"}
            max_duration = 30  # seconds — auto-stop if no response sent
            elapsed = 0
            while self._running and elapsed < max_duration:
                try:
                    await self._http.post(url, headers=headers)
                except Exception:
                    pass
                await asyncio.sleep(8)
                elapsed += 8

        self._typing_tasks[channel_id] = asyncio.create_task(typing_loop())

    async def _stop_typing(self, channel_id: str) -> None:
        """Stop typing indicator for a channel."""
        task = self._typing_tasks.pop(channel_id, None)
        if task:
            task.cancel()
