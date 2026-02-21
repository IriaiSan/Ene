"""Debounce + queue management for message intake.

Extracted from AgentLoop (Phase 6 refactor).
Manages the sliding-window debounce buffer and sequential processing queue:
    1. Buffer: messages accumulate per channel, flushed on timer or count
    2. Queue: batches wait for processing, merged if backlogged
    3. Processing: one batch at a time per channel, delegates to batch_processor

All state lives on AgentLoop — DebounceManager accesses it through
a back-reference (self._loop).
"""

from __future__ import annotations

import asyncio
import time as _time
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.events import InboundMessage


class DebounceManager:
    """Sliding-window debounce + sequential queue processor."""

    def __init__(self, loop: "AgentLoop") -> None:
        self._loop = loop

    # ── Debounce timer ────────────────────────────────────────────────

    async def debounce_timer(self, channel_key: str) -> None:
        """Wait for the debounce window, then flush batch to queue."""
        loop = self._loop
        await asyncio.sleep(loop._debounce_window)
        loop._debounce_timers.pop(channel_key, None)
        self.enqueue_batch(channel_key)

    # ── Enqueue ───────────────────────────────────────────────────────

    def enqueue_batch(self, channel_key: str) -> None:
        """Move current intake buffer into the processing queue."""
        loop = self._loop
        batch = loop._debounce_buffers.pop(channel_key, [])
        if not batch:
            return
        if channel_key not in loop._channel_queues:
            loop._channel_queues[channel_key] = []
        loop._channel_queues[channel_key].append(batch)
        logger.debug(f"Queue: enqueued {len(batch)} messages for {channel_key} (queue depth: {len(loop._channel_queues[channel_key])})")

        # Live trace — batch flushed from buffer to queue
        _trigger = "count" if len(batch) >= loop._debounce_batch_limit else "timer"
        loop._live.emit(
            "debounce_flush", channel_key,
            batch_size=len(batch),
            trigger=_trigger,
        )

        # Live trace — update state snapshot
        _active_mutes = sum(1 for exp in loop._muted_users.values() if exp > _time.time())
        loop._live.update_state(
            buffers={k: len(v) for k, v in loop._debounce_buffers.items()},
            queues={k: len(v) for k, v in loop._channel_queues.items()},
            muted_count=_active_mutes,
        )
        # Start queue processor if not already running
        existing = loop._queue_processors.get(channel_key)
        if not existing or existing.done():
            loop._queue_processors[channel_key] = asyncio.create_task(
                self.process_queue(channel_key)
            )

    # ── Queue processor ───────────────────────────────────────────────

    async def process_queue(self, channel_key: str) -> None:
        """Process batches from the queue, merging if backlogged.

        When multiple batches are waiting (LLM was slow, etc.), merges them
        into one mega-batch so Ene sees everything at once and responds to
        the most relevant — instead of processing stale batches one by one.
        """
        loop = self._loop
        queue = loop._channel_queues.get(channel_key)
        while queue:
            # Queue merge: if multiple batches waiting, collapse into one
            if len(queue) > 1:
                batches_to_merge = len(queue)
                merged: list["InboundMessage"] = []
                while queue:
                    merged.extend(queue.pop(0))

                # Cap merged batch to prevent token explosion — keep newest, drop oldest
                if len(merged) > loop._queue_merge_cap:
                    dropped_count = len(merged) - loop._queue_merge_cap
                    merged = merged[-loop._queue_merge_cap:]
                    logger.warning(
                        f"Queue merge: dropped {dropped_count} oldest messages "
                        f"(keeping {len(merged)} newest) in {channel_key}"
                    )
                    loop._live.emit(
                        "queue_merge_drop", channel_key,
                        dropped=dropped_count,
                        kept=len(merged),
                    )

                queue.append(merged)
                logger.info(
                    f"Queue merge: collapsed {batches_to_merge} batches → "
                    f"{len(merged)} messages in {channel_key}"
                )
                loop._live.emit(
                    "queue_merge", channel_key,
                    batches_merged=batches_to_merge,
                    total_messages=len(merged),
                )

            batch = queue.pop(0)
            try:
                await loop._process_batch(channel_key, batch)
            except Exception as e:
                logger.error(f"Queue: error processing batch in {channel_key}: {e}", exc_info=True)
        # Clean up empty queue
        loop._channel_queues.pop(channel_key, None)
