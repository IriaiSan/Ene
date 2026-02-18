"""Live event tracer for the real-time processing dashboard.

Collects processing events from the agent loop into a thread-safe
ring buffer. The dashboard SSE endpoint reads from this buffer to
stream events to the browser in real-time.

Architecture:
    loop.py emits events → LiveTracer (deque) → SSE → Browser
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from datetime import datetime
from typing import Any


class LiveTracer:
    """Real-time event tracer for the live dashboard.

    Thread-safe ring buffer that stores the last 500 events.
    Events are JSON-serializable dicts with a type, timestamp,
    channel_key, and type-specific payload.

    Also maintains a separate prompt log ring buffer (last 50 entries)
    for the full prompt detail panel — daemon prompts, Ene prompts, raw
    responses. These are large so they live in their own deque with their
    own SSE waiters.

    Usage from loop.py:
        self._live.emit("msg_arrived", msg.session_key,
                        sender="Dad", content_preview="mornin ene")
        self._live.emit_prompt("prompt_daemon", channel_key, ...)
    """

    def __init__(self, maxlen: int = 500) -> None:
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._counter: int = 0
        # asyncio events for SSE push notification
        self._waiters: list[asyncio.Event] = []
        # Snapshot data for the state panel (updated by loop.py)
        self._state: dict[str, Any] = {
            "buffers": {},       # channel_key → buffer size
            "queues": {},        # channel_key → queue depth
            "processing": None,  # channel_key currently being processed
            "muted_count": 0,
            "active_batch": None,
        }
        self._state_lock = threading.Lock()
        # Prompt log — separate ring buffer for full prompt/response content
        self._prompts: deque[dict[str, Any]] = deque(maxlen=50)
        self._prompt_lock = threading.Lock()
        self._prompt_counter: int = 0
        self._prompt_waiters: list[asyncio.Event] = []

    def emit(self, event_type: str, channel_key: str = "", **data: Any) -> None:
        """Emit a processing event. Called from the agent loop.

        Thread-safe. Wakes any SSE waiters so events are pushed immediately.
        All values in **data must be JSON-serializable (str, int, float, bool, list, dict, None).
        """
        with self._lock:
            self._counter += 1
            event: dict[str, Any] = {
                "id": self._counter,
                "type": event_type,
                "ts": datetime.now().strftime("%H:%M:%S.%f")[:12],
                "channel_key": channel_key,
                **data,
            }
            self._events.append(event)

        # Wake all SSE waiters (non-blocking). Iterate a copy — waiters remove
        # themselves in the wait_for_event finally block when woken.
        for waiter in list(self._waiters):
            waiter.set()

    def get_events_since(self, last_id: int) -> list[dict[str, Any]]:
        """Return events with id > last_id. For SSE catch-up on reconnect."""
        with self._lock:
            return [e for e in self._events if e["id"] > last_id]

    def get_recent(self, count: int = 100) -> list[dict[str, Any]]:
        """Return the most recent N events."""
        with self._lock:
            items = list(self._events)
            return items[-count:] if len(items) > count else items

    def emit_prompt(self, prompt_type: str, channel_key: str = "", **data: Any) -> None:
        """Emit a full prompt/response entry to the prompt log.

        Same interface as emit() but goes to the separate prompt deque.
        prompt_type values: prompt_daemon, prompt_ene, prompt_ene_response
        """
        with self._prompt_lock:
            self._prompt_counter += 1
            entry: dict[str, Any] = {
                "id": self._prompt_counter,
                "type": prompt_type,
                "ts": datetime.now().strftime("%H:%M:%S.%f")[:12],
                "channel_key": channel_key,
                **data,
            }
            self._prompts.append(entry)

        for waiter in list(self._prompt_waiters):
            waiter.set()

    def get_prompts_since(self, last_id: int) -> list[dict[str, Any]]:
        """Return prompt entries with id > last_id."""
        with self._prompt_lock:
            return [p for p in self._prompts if p["id"] > last_id]

    async def wait_for_prompt(self, timeout: float = 30.0) -> bool:
        """Async wait until a new prompt entry is emitted."""
        waiter = asyncio.Event()
        self._prompt_waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            if waiter in self._prompt_waiters:
                self._prompt_waiters.remove(waiter)

    def update_state(self, **kwargs: Any) -> None:
        """Update the live state snapshot. Called from loop.py."""
        with self._state_lock:
            self._state.update(kwargs)

    def get_state(self) -> dict[str, Any]:
        """Return a copy of the current state snapshot."""
        with self._state_lock:
            return dict(self._state)

    async def wait_for_event(self, timeout: float = 30.0) -> bool:
        """Async wait until a new event is emitted.

        Returns True if an event was emitted, False on timeout.
        Used by the SSE endpoint to push events immediately.
        """
        waiter = asyncio.Event()
        self._waiters.append(waiter)
        try:
            await asyncio.wait_for(waiter.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False
        finally:
            self._waiters.remove(waiter)

    def hard_reset(self) -> None:
        """Clear all events and reset state. Called by the dashboard hard-reset button.

        Emits a single 'hard_reset' event after clearing so the SSE clients
        know to wipe their timelines.
        """
        with self._lock:
            self._events.clear()
            self._counter = 0
        with self._prompt_lock:
            self._prompts.clear()
            self._prompt_counter = 0
        with self._state_lock:
            self._state = {
                "buffers": {},
                "queues": {},
                "processing": None,
                "muted_count": 0,
                "active_batch": None,
            }
        # Emit reset marker so connected clients know to clear their view
        self.emit("hard_reset", "", message="Pipeline hard reset by dashboard")

    @property
    def last_id(self) -> int:
        """The ID of the most recent event (0 if none)."""
        with self._lock:
            return self._counter
