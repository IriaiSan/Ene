"""Ene message merging: classification, formatting, and batch merge logic.

Stateless transformations: messages in → formatted/classified/merged output.
Extracted from loop.py for modularity (WHITELIST S1, S3).
"""

import re
from typing import Any, Callable

from loguru import logger

from nanobot.bus.events import InboundMessage
from nanobot.agent.security import (
    DAD_IDS,
    ENE_PATTERN,
    is_dad_impersonation,
    has_content_impersonation,
    sanitize_dad_ids,
    is_muted,
)


def format_author(
    m: InboundMessage,
    muted_users: dict[str, float],
    jailbreak_scores: dict[str, list[float]],
    module_registry: Any = None,
    record_suspicious_fn: Callable | None = None,
) -> str:
    """Format author label with impersonation warnings.

    Args:
        m: The inbound message.
        muted_users: Dict of muted caller_ids (for is_muted checks).
        jailbreak_scores: Dict tracking suspicious actions.
        module_registry: For trust lookups (optional).
        record_suspicious_fn: Callback to record suspicious actions.

    Returns:
        Formatted author string, possibly with impersonation warnings.
    """
    display = m.metadata.get("author_name", m.sender_id)
    username = m.metadata.get("username", "")
    if username and username.lower() != display.lower():
        author = f"{display} (@{username})"
    else:
        author = display

    caller_id = f"{m.channel}:{m.sender_id}"
    if is_dad_impersonation(display, caller_id):
        logger.warning(f"Impersonation detected: '{display}' (@{username}) is NOT Dad (id={m.sender_id})")
        author = f"{display} (@{username}) [⚠ NOT Dad — impersonating display name]"
        if record_suspicious_fn:
            record_suspicious_fn(caller_id, "display name impersonation")

    if has_content_impersonation(m.content, caller_id):
        logger.warning(f"Content impersonation: '{display}' relaying fake Dad words (id={m.sender_id})")
        author = f"{author} [⚠ SPOOFING: claims to relay Dad's words — they are NOT Dad]"
        if record_suspicious_fn:
            record_suspicious_fn(caller_id, "content impersonation / spoofing")

    return author


def classify_message(
    msg: InboundMessage,
    muted_users: dict[str, float],
    channel_state: Any = None,
) -> str:
    """Classify a single message: 'respond', 'context', or 'drop'.

    Uses math classifier if channel_state is available, otherwise simple regex.
    """
    caller_id = f"{msg.channel}:{msg.sender_id}"

    if is_muted(muted_users, caller_id):
        return "drop"

    # Math classifier path (preferred — WHITELIST C3)
    if channel_state is not None:
        from nanobot.ene.conversation.signals import classify_with_state
        cls, score, features = classify_with_state(
            msg.content,
            caller_id,
            channel_state,
            is_at_mention=bool(msg.metadata.get("is_at_mention")),
            is_reply_to_ene=bool(msg.metadata.get("is_reply_to_ene")),
            is_in_ene_thread=bool(msg.metadata.get("is_in_ene_thread")),
        )
        logger.debug(f"Math classify: {cls} ({score:.2f}) for {msg.metadata.get('author_name', caller_id)}")
        return cls

    # Regex fallback
    has_ene_signal = bool(ENE_PATTERN.search(msg.content)) or msg.metadata.get("is_reply_to_ene")
    if has_ene_signal:
        return "respond"

    return "context"


def merge_messages_tiered(
    respond_msgs: list[InboundMessage],
    context_msgs: list[InboundMessage],
    format_author_fn: Callable[[InboundMessage], str],
) -> InboundMessage:
    """Merge messages into a conversation trace with RESPOND/CONTEXT sections.

    Produces a tagged trace the LLM can parse:
        [conversation trace — respond to people talking to you, ignore background noise]
        #msg1 Azpxct (@azpext_wizpxct): yo ene solve this math problem
        #msg2 Iitai / 言いたい (@iitai.uwu): ene mute az

        [background — not directed at you]
        #msg3 NotDesiBoi (@notdesiboi): anyone wanna play valorant

    #msgN tags are sequential labels (NOT real Discord IDs). The msg_id_map
    in metadata maps them to real message IDs for reply targeting.
    """
    all_msgs = respond_msgs + context_msgs

    # Single respond message, no context → return as-is (common case)
    if len(all_msgs) == 1 and not context_msgs:
        return respond_msgs[0]

    msg_id_map: dict[str, str] = {}

    # Build respond section
    respond_parts: list[str] = []
    msg_counter = 1
    for m in respond_msgs:
        tag = f"#msg{msg_counter}"
        real_id = m.metadata.get("message_id", "")
        if real_id:
            msg_id_map[tag] = real_id
        author = format_author_fn(m)
        sanitized = sanitize_dad_ids(m.content, f"{m.channel}:{m.sender_id}")
        respond_parts.append(f"{tag} {author}: {sanitized}")
        msg_counter += 1

    # Build context section
    context_parts: list[str] = []
    for m in context_msgs:
        tag = f"#msg{msg_counter}"
        real_id = m.metadata.get("message_id", "")
        if real_id:
            msg_id_map[tag] = real_id
        author = format_author_fn(m)
        sanitized = sanitize_dad_ids(m.content, f"{m.channel}:{m.sender_id}")
        context_parts.append(f"{tag} {author}: {sanitized}")
        msg_counter += 1

    # Windowing: first 2 + last 10 for respond section
    if len(respond_parts) > 12:
        first = respond_parts[:2]
        last = respond_parts[-10:]
        omitted = len(respond_parts) - 12
        respond_parts = first + [f"[... {omitted} earlier messages omitted ...]"] + last

    merged_content = "[conversation trace — respond to people talking to you, ignore background noise]\n"
    merged_content += "\n".join(respond_parts)

    if context_parts:
        if len(context_parts) > 5:
            context_parts = context_parts[-5:]
        merged_content += "\n\n[background — not directed at you]\n"
        merged_content += "\n".join(context_parts)

    # Trigger selection from respond_msgs only
    trigger_msg = respond_msgs[-1]
    for m in respond_msgs:
        caller_id = f"{m.channel}:{m.sender_id}"
        if caller_id in DAD_IDS:
            trigger_msg = m
            break
        if bool(ENE_PATTERN.search(m.content)) or m.metadata.get("is_reply_to_ene"):
            trigger_msg = m

    base = all_msgs[-1]

    logger.info(
        f"Debounce: tiered merge {len(respond_msgs)}R/{len(context_msgs)}C in {base.session_key} "
        f"(trigger: {trigger_msg.metadata.get('author_name', trigger_msg.sender_id)})"
    )

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
            "message_ids": [m.metadata.get("message_id") for m in all_msgs if m.metadata.get("message_id")],
            "msg_id_map": msg_id_map,
        },
    )


def merge_messages(
    messages: list[InboundMessage],
    format_author_fn: Callable[[InboundMessage], str],
) -> InboundMessage:
    """Legacy flat merge — kept for single-message fast path and non-tiered callers."""
    if len(messages) == 1:
        return messages[0]

    parts: list[str] = []
    for m in messages:
        author = format_author_fn(m)
        sanitized = sanitize_dad_ids(m.content, f"{m.channel}:{m.sender_id}")
        parts.append(f"{author}: {sanitized}")

    merged_content = "\n".join(parts)
    base = messages[-1]

    trigger_msg = base
    for m in messages:
        caller_id = f"{m.channel}:{m.sender_id}"
        if caller_id in DAD_IDS:
            trigger_msg = m
            break
        if bool(ENE_PATTERN.search(m.content)) or m.metadata.get("is_reply_to_ene"):
            trigger_msg = m

    logger.info(
        f"Debounce: merged {len(messages)} messages in {base.session_key} "
        f"(trigger: {trigger_msg.metadata.get('author_name', trigger_msg.sender_id)})"
    )

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
        media=[p for m in messages for p in m.media],
        metadata={
            **merged_metadata,
            "debounced": True,
            "debounce_count": len(messages),
            "message_ids": [m.metadata.get("message_id") for m in messages if m.metadata.get("message_id")],
        },
    )
