"""Multi-thread context formatter.

Produces the formatted conversation context string that the LLM receives,
replacing the flat _merge_messages_tiered() output with thread-aware structure.

Output format shows Ene's active threads with first+last windowing,
background threads, and unthreaded standalone messages.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Callable

# Word-boundary match to avoid false positives ("generic", "scene", etc.)
_ENE_PATTERN = re.compile(r"\bene\b", re.IGNORECASE)

from .models import (
    ACTIVE,
    BG_THREAD_LAST_N,
    BG_THREAD_MAX_DISPLAY,
    DEAD,
    ENE_THREAD_FIRST_N,
    ENE_THREAD_LAST_N,
    ENE_THREAD_MAX_DISPLAY,
    ENE_THREAD_SHORT_THRESHOLD,
    STALE,
    PendingMessage,
    Thread,
    ThreadMessage,
)

if TYPE_CHECKING:
    from nanobot.bus.events import InboundMessage


def _format_age(created_at: float, now: float | None = None) -> str:
    """Format thread age as human-readable string."""
    now = now or time.time()
    age = now - created_at
    if age < 60:
        return f"{int(age)}s ago"
    elif age < 3600:
        return f"{int(age / 60)} min ago"
    elif age < 86400:
        return f"{int(age / 3600)}h ago"
    else:
        return f"{int(age / 86400)}d ago"


def _format_thread_message(
    tm: ThreadMessage,
    msg_counter: int,
    msg_id_map: dict[str, str],
    *,
    with_tag: bool = False,
) -> tuple[str, int]:
    """Format a single thread message.

    Only includes a #msgN reply tag when with_tag=True. Tags are reserved
    for the latest non-Ene messages that Ene should reply to — most messages
    in threads are plain dialogue (no tags, no noise).

    Returns (formatted_line, next_counter).
    """
    if with_tag:
        tag = f"#msg{msg_counter}"
        if tm.discord_msg_id:
            msg_id_map[tag] = tm.discord_msg_id
        prefix = f"{tag} "
        next_counter = msg_counter + 1
    else:
        prefix = ""
        next_counter = msg_counter

    if tm.is_ene:
        line = f"{prefix}Ene: {tm.content}"
    elif tm.author_username and tm.author_username != tm.author_name:
        line = f"{prefix}{tm.author_name} (@{tm.author_username}): {tm.content}"
    else:
        line = f"{prefix}{tm.author_name}: {tm.content}"

    return line, next_counter


def _last_non_ene_index(messages: list[ThreadMessage]) -> int:
    """Find the index of the last non-Ene message in a list.

    Returns -1 if no non-Ene messages exist.
    """
    for i in range(len(messages) - 1, -1, -1):
        if not messages[i].is_ene:
            return i
    return -1


def _format_ene_thread(
    thread: Thread,
    msg_counter: int,
    msg_id_map: dict[str, str],
    now: float | None = None,
) -> tuple[list[str], int]:
    """Format an Ene-involved thread.

    Two modes:
    - First time (last_shown_index==0): full first+gap+last windowing
    - Follow-up (last_shown_index>0): only NEW messages since Ene last responded

    This prevents re-replaying the entire thread history every batch.
    Returns (lines, next_counter).
    """
    now = now or time.time()
    lines: list[str] = []

    age = _format_age(thread.created_at, now)
    count = thread.message_count
    state = thread.state

    # Participant names (deduplicate, exclude "Ene" duplicate listing)
    participant_names = []
    seen = set()
    for msg in thread.messages:
        name = "Ene" if msg.is_ene else msg.author_name
        if name not in seen:
            participant_names.append(name)
            seen.add(name)

    # ── Follow-up mode: only show new messages since last response ──
    new_msgs = thread.messages[thread.last_shown_index:]
    if thread.last_shown_index > 0:
        if not new_msgs:
            # Thread has no new messages but is still active — show a brief
            # reminder so the LLM knows this conversation exists. Skipping it
            # entirely caused threads to vanish from the prompt.
            last_activity = _format_age(thread.updated_at, now)
            lines.append(
                f"--- Thread (no new messages, last activity {last_activity}) ---"
            )
            lines.append(f"Participants: {', '.join(participant_names)}")
            return lines, msg_counter
        already = thread.last_shown_index
        lines.append(
            f"--- Thread (continued, {len(new_msgs)} new): "
            f"{already} earlier messages already in your history ---"
        )
        lines.append(f"Participants: {', '.join(participant_names)}")
        # Only the last non-Ene message gets a reply tag
        last_non_ene_idx = _last_non_ene_index(new_msgs)
        for i, tm in enumerate(new_msgs):
            line, msg_counter = _format_thread_message(
                tm, msg_counter, msg_id_map,
                with_tag=(i == last_non_ene_idx),
            )
            lines.append(line)
        return lines, msg_counter

    # ── First time: full windowing ──────────────────────────────────
    lines.append(f"--- Thread: started {age}, {count} messages ({state}) ---")
    lines.append(f"Participants: {', '.join(participant_names)}")

    if count <= ENE_THREAD_SHORT_THRESHOLD:
        # Short thread — show all messages, tag only last non-Ene
        last_non_ene_idx = _last_non_ene_index(thread.messages)
        for i, tm in enumerate(thread.messages):
            line, msg_counter = _format_thread_message(
                tm, msg_counter, msg_id_map,
                with_tag=(i == last_non_ene_idx),
            )
            lines.append(line)
    else:
        # Long thread — first N + gap + last N, tag only last non-Ene in last N
        first_msgs = thread.messages[:ENE_THREAD_FIRST_N]
        last_msgs = thread.messages[-ENE_THREAD_LAST_N:]
        omitted = count - ENE_THREAD_FIRST_N - ENE_THREAD_LAST_N

        for tm in first_msgs:
            line, msg_counter = _format_thread_message(tm, msg_counter, msg_id_map)
            lines.append(line)

        lines.append(f"[... {omitted} earlier messages omitted ...]")

        last_non_ene_idx = _last_non_ene_index(last_msgs)
        for i, tm in enumerate(last_msgs):
            line, msg_counter = _format_thread_message(
                tm, msg_counter, msg_id_map,
                with_tag=(i == last_non_ene_idx),
            )
            lines.append(line)

    return lines, msg_counter


def _format_bg_thread(
    thread: Thread,
    msg_counter: int,
    msg_id_map: dict[str, str],
    now: float | None = None,
) -> tuple[list[str], int]:
    """Format a background thread (last N messages only).

    Returns (lines, next_counter).
    """
    now = now or time.time()
    lines: list[str] = []

    age = _format_age(thread.created_at, now)
    count = thread.message_count
    state = thread.state

    participant_names = []
    seen = set()
    for msg in thread.messages:
        if msg.author_name not in seen:
            participant_names.append(msg.author_name)
            seen.add(msg.author_name)

    lines.append(f"--- Thread: started {age}, {count} messages ({state}) ---")
    lines.append(f"Participants: {', '.join(participant_names)}")

    display_msgs = thread.messages[-BG_THREAD_LAST_N:]
    for tm in display_msgs:
        line, msg_counter = _format_thread_message(tm, msg_counter, msg_id_map)
        lines.append(line)

    return lines, msg_counter


def _select_trigger(
    respond_msgs: list["InboundMessage"],
    context_msgs: list["InboundMessage"],
) -> "InboundMessage":
    """Select the trigger message (same logic as _merge_messages_tiered).

    Priority: Dad > first Ene mention/reply > last respond message.
    """
    from nanobot.agent.security import DAD_IDS

    if not respond_msgs:
        return (context_msgs or respond_msgs)[-1]

    trigger = respond_msgs[-1]
    for m in respond_msgs:
        caller_id = f"{m.channel}:{m.sender_id}"
        if caller_id in DAD_IDS:
            trigger = m
            break
        if bool(_ENE_PATTERN.search(m.content)) or m.metadata.get("is_reply_to_ene"):
            trigger = m

    return trigger


def build_threaded_context(
    threads: dict[str, Thread],
    pending: list[PendingMessage],
    respond_msgs: list["InboundMessage"],
    context_msgs: list["InboundMessage"],
    channel_key: str,
    format_author_fn: Callable | None = None,
) -> "InboundMessage":
    """Build a multi-thread aware merged message.

    Returns an InboundMessage with the same metadata contract as
    _merge_messages_tiered (msg_id_map, debounced, etc.).
    """
    from nanobot.bus.events import InboundMessage

    all_msgs = respond_msgs + context_msgs
    now = time.time()

    # Single message, no threads tracked → fast path (no formatting overhead)
    channel_pending = [pm for pm in pending if pm.channel_key == channel_key]
    if len(all_msgs) == 1 and not context_msgs:
        channel_threads = [
            t
            for t in threads.values()
            if t.channel_key == channel_key and t.state != DEAD
        ]
        # Only use fast path if there are no threads with new messages and no pending.
        # If any thread has new messages since last_shown_index, we must format them
        # so the LLM gets the full context (not just the raw single message).
        threads_with_new = [
            t for t in channel_threads
            if len(t.messages) > t.last_shown_index
        ]
        if not threads_with_new and not channel_pending:
            return respond_msgs[0]

    # Gather threads for this channel
    channel_threads = [
        t
        for t in threads.values()
        if t.channel_key == channel_key and t.state != DEAD
    ]

    ene_threads = sorted(
        [t for t in channel_threads if t.ene_involved],
        key=lambda t: t.updated_at,
        reverse=True,  # Most recent first
    )
    bg_threads = sorted(
        [t for t in channel_threads if not t.ene_involved],
        key=lambda t: t.updated_at,
        reverse=True,
    )

    # Unthreaded pending messages for this channel (already computed for fast path)
    unthreaded = channel_pending

    msg_id_map: dict[str, str] = {}
    msg_counter = 1
    parts: list[str] = []

    # ── Ene threads section ──────────────────────────────────────
    displayed_ene_threads: list[Thread] = []
    if ene_threads:
        parts.append("[active conversations — you are part of these threads]\n")
        for t in ene_threads[:ENE_THREAD_MAX_DISPLAY]:
            lines, msg_counter = _format_ene_thread(t, msg_counter, msg_id_map, now)
            parts.extend(lines)
            parts.append("")  # Blank line between threads
            displayed_ene_threads.append(t)

        if len(ene_threads) > ENE_THREAD_MAX_DISPLAY:
            omitted = len(ene_threads) - ENE_THREAD_MAX_DISPLAY
            parts.append(f"[... {omitted} more threads you're part of, not shown ...]\n")

    # ── Background threads section ───────────────────────────────
    if bg_threads:
        parts.append("[background conversations — not directed at you, just awareness]\n")
        for t in bg_threads[:BG_THREAD_MAX_DISPLAY]:
            lines, msg_counter = _format_bg_thread(t, msg_counter, msg_id_map, now)
            parts.extend(lines)
            parts.append("")

    # ── Unthreaded messages ──────────────────────────────────────
    if unthreaded:
        parts.append("[unthreaded]\n")
        last_idx = len(unthreaded[-5:]) - 1
        for i, pm in enumerate(unthreaded[-5:]):  # Last 5 unthreaded
            # Tag the last unthreaded message — Ene might reply to it
            line, msg_counter = _format_thread_message(
                pm.message, msg_counter, msg_id_map,
                with_tag=(i == last_idx),
            )
            parts.append(line)
        parts.append("")

    # ── If nothing was formatted (no threads, no pending) ────────
    # This can happen if messages were just ingested and all went to pending,
    # or if the tracker state is empty. Fall through with raw messages.
    if not parts:
        # Build a minimal trace from the current batch.
        # Only tag the last respond message — that's what Ene should reply to.
        parts.append("[conversation trace]\n")
        last_respond_idx = len(respond_msgs) - 1
        for i, m in enumerate(respond_msgs):
            author = m.metadata.get("author_name", m.sender_id)
            username = m.metadata.get("username", "")
            use_tag = (i == last_respond_idx)
            if use_tag:
                tag = f"#msg{msg_counter}"
                real_id = m.metadata.get("message_id", "")
                if real_id:
                    msg_id_map[tag] = real_id
                prefix = f"{tag} "
                msg_counter += 1
            else:
                prefix = ""
            if username and username != author:
                parts.append(f"{prefix}{author} (@{username}): {m.content}")
            else:
                parts.append(f"{prefix}{author}: {m.content}")

        if context_msgs:
            # Background messages — no tags (not directed at Ene)
            parts.append("\n[background — not directed at you]\n")
            for m in context_msgs[-5:]:
                author = m.metadata.get("author_name", m.sender_id)
                parts.append(f"{author}: {m.content}")

    merged_content = "\n".join(parts).strip()

    # ── Mark threads as shown so follow-up turns only show NEW messages ──
    for t in displayed_ene_threads:
        t.last_shown_index = len(t.messages)

    # ── Build the InboundMessage ─────────────────────────────────
    trigger_msg = _select_trigger(respond_msgs, context_msgs)
    base = all_msgs[-1]

    merged_metadata = {**base.metadata}
    if trigger_msg is not base:
        for key in ("author_name", "display_name", "first_name", "username"):
            if key in trigger_msg.metadata:
                merged_metadata[key] = trigger_msg.metadata[key]

    return InboundMessage(
        channel=trigger_msg.channel,
        sender_id=trigger_msg.sender_id,
        chat_id=base.chat_id,
        content=merged_content,
        timestamp=base.timestamp,
        media=[p for m in all_msgs for p in m.media],
        metadata={
            **merged_metadata,
            "debounced": True,
            "debounce_count": len(all_msgs),
            "message_ids": [
                m.metadata.get("message_id")
                for m in all_msgs
                if m.metadata.get("message_id")
            ],
            "msg_id_map": msg_id_map,
            "thread_count": len(ene_threads),
            "bg_thread_count": len(bg_threads),
        },
    )


def build_single_thread_context(
    focus_thread: Thread,
    all_threads: dict[str, Thread],
    pending: list[PendingMessage],
    channel_key: str,
) -> tuple[str, dict[str, str], str | None]:
    """Build focused context for a single thread's LLM call.

    Shows the focus thread as the primary conversation and all other
    threads as background context. Does NOT mutate last_shown_index —
    the caller (per-thread loop) handles that after response.

    Returns:
        (content, msg_id_map, primary_author_name)
    """
    now = time.time()
    msg_id_map: dict[str, str] = {}
    msg_counter = 1
    parts: list[str] = []

    # ── Focus thread (primary) ────────────────────────────────────
    # Show only new messages since last shown
    new_msgs = focus_thread.messages[focus_thread.last_shown_index:]
    if not new_msgs:
        new_msgs = focus_thread.messages[-4:]  # Fallback: show last 4

    parts.append("[your conversation — respond to this thread]\n")

    if focus_thread.last_shown_index > 0:
        already = focus_thread.last_shown_index
        parts.append(
            f"--- Thread (continued, {len(new_msgs)} new): "
            f"{already} earlier messages already in your history ---"
        )
    else:
        age = _format_age(focus_thread.created_at, now)
        parts.append(
            f"--- Thread: started {age}, {focus_thread.message_count} messages "
            f"({focus_thread.state}) ---"
        )

    # Participant names
    participant_names = []
    seen = set()
    for msg in focus_thread.messages:
        name = "Ene" if msg.is_ene else msg.author_name
        if name not in seen:
            participant_names.append(name)
            seen.add(name)
    parts.append(f"Participants: {', '.join(participant_names)}")

    # Only tag the last non-Ene message — that's what Ene should reply to
    last_non_ene_idx = _last_non_ene_index(new_msgs)
    for i, tm in enumerate(new_msgs):
        line, msg_counter = _format_thread_message(
            tm, msg_counter, msg_id_map,
            with_tag=(i == last_non_ene_idx),
        )
        parts.append(line)
    parts.append("")

    # Determine primary author (non-Ene sender who spoke most recently)
    primary_author_name = None
    for tm in reversed(new_msgs):
        if not tm.is_ene:
            primary_author_name = tm.author_name
            break

    # ── Other threads as background ───────────────────────────────
    channel_threads = [
        t for t in all_threads.values()
        if t.channel_key == channel_key
        and t.state != DEAD
        and t.thread_id != focus_thread.thread_id
    ]

    other_ene = [t for t in channel_threads if t.ene_involved]
    bg = [t for t in channel_threads if not t.ene_involved]

    if other_ene or bg:
        parts.append("[background — other conversations happening around you]\n")
        for t in sorted(other_ene + bg, key=lambda t: t.updated_at, reverse=True)[:3]:
            lines, msg_counter = _format_bg_thread(t, msg_counter, msg_id_map, now)
            parts.extend(lines)
            parts.append("")

    # ── Unthreaded pending ────────────────────────────────────────
    channel_pending = [pm for pm in pending if pm.channel_key == channel_key]
    if channel_pending:
        parts.append("[unthreaded]\n")
        for pm in channel_pending[-3:]:
            line, msg_counter = _format_thread_message(
                pm.message, msg_counter, msg_id_map
            )
            parts.append(line)

    content = "\n".join(parts).strip()
    return content, msg_id_map, primary_author_name
