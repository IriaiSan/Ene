"""Batch processing pipeline — classify, merge, and dispatch messages.

Extracted from AgentLoop._process_batch() (Phase 3 refactor).
The pipeline:
    1. Tag stale messages
    2. Check/rotate session budget
    3. Classify each message (daemon → math fallback → hard override)
    4. Merge classified messages (thread-aware or flat)
    5. Dispatch to LLM (multi-thread or single-thread path)

All state lives on AgentLoop — BatchProcessor accesses it through a
back-reference (self._loop). This keeps AgentLoop as the single state
owner while making the pipeline stages readable and testable.
"""

from __future__ import annotations

import asyncio
import time as _time
from datetime import datetime, timedelta
from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.agent.security import DAD_IDS, ENE_PATTERN
from nanobot.session.manager import Session

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop


# Stale threshold: messages older than this are tagged _is_stale
_STALE_THRESHOLD = timedelta(minutes=5)

# Auto-rotation triggers at this fraction of budget
_ROTATION_THRESHOLD = 0.8

# Token budget for session history
_HISTORY_TOKEN_BUDGET = 60_000

# Max threads to process per batch cycle
_THREAD_CAP = 3


class BatchProcessor:
    """Orchestrates batch classification, context building, and LLM dispatch.

    Each method corresponds to a pipeline stage. The public entry point
    is ``process()``, which runs the stages sequentially.
    """

    def __init__(self, loop: "AgentLoop") -> None:
        self._loop = loop

    # ── Public entry point ────────────────────────────────────────────

    async def process(self, channel_key: str, messages: list[InboundMessage]) -> None:
        """Process a batch of messages — classify, merge, and send to LLM.

        Called from AgentLoop._process_queue. No re-buffering needed — the
        queue handles sequential processing.
        """
        if not messages:
            return

        loop = self._loop

        # Generate trace_id — links all module events across the pipeline
        loop._batch_counter += 1
        trace_id = f"{int(_time.time())}_{channel_key}_{loop._batch_counter}"
        for metrics in loop._module_metrics.values():
            metrics.set_trace_id(trace_id)

        self._tag_stale_messages(messages)
        await self._check_session_rotation(channel_key)

        # Classify
        respond_msgs, context_msgs = await self._classify_batch(
            channel_key, messages,
        )

        dropped = len(messages) - len(respond_msgs) - len(context_msgs)
        if dropped:
            logger.debug(f"Debounce: dropped {dropped} messages in {channel_key}")

        # No respond messages → lurk only (no LLM call)
        if not respond_msgs:
            if context_msgs:
                self._lurk_context(channel_key, context_msgs)
            return

        # Merge + dispatch
        merged = self._merge_messages(channel_key, respond_msgs, context_msgs)

        # Live trace — merge complete + active batch state
        _thread_count = (merged.metadata or {}).get("thread_count", 0)
        loop._live.emit(
            "merge_complete", channel_key,
            respond_count=len(respond_msgs),
            context_count=len(context_msgs),
            dropped_count=dropped,
            thread_count=_thread_count,
        )
        loop._live.update_state(
            processing=channel_key,
            active_batch={
                "channel_key": channel_key,
                "msg_count": len(respond_msgs) + len(context_msgs),
                "respond": len(respond_msgs),
                "context": len(context_msgs),
                "dropped": dropped,
            },
        )

        # Security check on merged content
        loop._check_auto_mute(merged)

        # Dispatch to LLM
        await self._dispatch(channel_key, merged, respond_msgs, context_msgs)

    # ── Stage 1: Stale message tagging ────────────────────────────────

    def _tag_stale_messages(self, messages: list[InboundMessage]) -> None:
        """Tag messages that sat in the queue too long."""
        _now = datetime.now()
        for m in messages:
            msg_age = _now - m.timestamp
            if msg_age > _STALE_THRESHOLD:
                m.metadata["_is_stale"] = True
                m.metadata["_stale_minutes"] = int(msg_age.total_seconds() / 60)

    # ── Stage 2: Session rotation check ───────────────────────────────

    async def _check_session_rotation(self, channel_key: str) -> None:
        """Auto-rotate session if at 80% of token budget."""
        loop = self._loop
        session = loop.sessions.get_or_create(channel_key)
        estimated_tokens = session.estimate_tokens()

        if estimated_tokens <= _HISTORY_TOKEN_BUDGET * _ROTATION_THRESHOLD:
            return

        logger.warning(
            f"Session {channel_key} at {estimated_tokens} tokens "
            f"(~{estimated_tokens * 100 // _HISTORY_TOKEN_BUDGET}% budget), auto-rotating"
        )

        # Generate summary BEFORE clearing
        _rotation_summary = loop._session_summaries.get(channel_key, "")
        if not _rotation_summary and len(session.messages) > 5:
            try:
                _rotation_summary = await loop._generate_running_summary(session, channel_key) or ""
            except Exception:
                _rotation_summary = ""

        messages_to_archive = list(session.messages)
        session.messages.clear()

        # Inject summary as seed context for new session
        if _rotation_summary:
            session.messages.append({
                "role": "system",
                "content": f"[Previous session summary: {_rotation_summary}]",
            })

        loop.sessions.save(session)
        loop.sessions.invalidate(session.key)
        loop._session_summaries.pop(channel_key, None)
        loop._summary_msg_counters.pop(channel_key, None)

        # Background consolidation of archived messages
        async def _consolidate_old(_msgs=messages_to_archive, _key=channel_key):
            temp_session = Session(key=_key)
            temp_session.messages = _msgs
            await loop._consolidate_memory(temp_session, archive_all=True)
        asyncio.create_task(_consolidate_old())

        logger.info(
            f"Auto-rotated session {channel_key} ({estimated_tokens} tokens), "
            f"summary {'injected' if _rotation_summary else 'empty'}"
        )

    # ── Stage 3: Per-message classification ───────────────────────────

    async def _classify_batch(
        self,
        channel_key: str,
        messages: list[InboundMessage],
    ) -> tuple[list[InboundMessage], list[InboundMessage]]:
        """Classify each message as respond/context/drop using daemon + fallback.

        Returns:
            (respond_msgs, context_msgs). Dropped messages are silently discarded.
        """
        loop = self._loop
        respond_msgs: list[InboundMessage] = []
        context_msgs: list[InboundMessage] = []
        daemon_mod = loop.module_registry.get_module("daemon")

        # Get channel state for math-based classification
        _channel_state = None
        conv_mod = loop.module_registry.get_module("conversation_tracker")
        if conv_mod and hasattr(conv_mod, "tracker") and conv_mod.tracker:
            _channel_state = conv_mod.tracker.get_channel_state(channel_key)

        for m in messages:
            caller_id = f"{m.channel}:{m.sender_id}"
            is_dad = caller_id in DAD_IDS

            # Fast path: muted → DROP
            if loop._is_muted(caller_id):
                continue

            # Try daemon classification first
            if daemon_mod and hasattr(daemon_mod, "process_message"):
                try:
                    classified = await self._classify_with_daemon(
                        m, channel_key, caller_id, is_dad,
                        daemon_mod, conv_mod, _channel_state,
                        respond_msgs, context_msgs,
                    )
                    if classified:
                        continue
                except Exception as e:
                    logger.debug(f"Daemon failed for message, falling back: {e}")

            # Fallback: hardcoded classification
            tier = loop._classify_message(m, _channel_state)
            loop._live.emit(
                "classification", channel_key,
                sender=m.metadata.get("author_name", m.sender_id),
                result=tier,
                source="fallback",
            )
            if tier == "respond":
                respond_msgs.append(m)
            elif tier == "context":
                context_msgs.append(m)
            # "drop" → silently discarded

        return respond_msgs, context_msgs

    async def _classify_with_daemon(
        self,
        m: InboundMessage,
        channel_key: str,
        caller_id: str,
        is_dad: bool,
        daemon_mod: Any,
        conv_mod: Any,
        channel_state: Any,
        respond_msgs: list[InboundMessage],
        context_msgs: list[InboundMessage],
    ) -> bool:
        """Classify a single message using the daemon. Returns True if handled.

        Handles daemon prompt logging, result processing, hard overrides,
        staleness downgrades, and auto-muting.
        """
        loop = self._loop
        _sender_label = m.metadata.get("author_name", m.sender_id)

        # Get recent channel context for daemon
        _recent_context: list[str] = []
        if conv_mod and hasattr(conv_mod, "tracker") and conv_mod.tracker:
            _recent_context = conv_mod.tracker.get_recent_context(channel_key, limit=8)

        # Build daemon prompt and log it
        _daemon_user_msg = f"Sender: {_sender_label} (ID: {caller_id})"
        if is_dad:
            _daemon_user_msg += " [THIS IS DAD - respond unless clearly talking to someone else]"
        if m.metadata.get("is_reply_to_ene"):
            _daemon_user_msg += " [REPLYING TO ENE]"
        if m.metadata.get("_is_stale"):
            _daemon_user_msg += f" [MESSAGE IS STALE - sent {m.metadata.get('_stale_minutes', '?')} min ago]"
        if _recent_context:
            _daemon_user_msg += "\n\nRecent chat:\n" + "\n".join(_recent_context)
        _daemon_user_msg += f"\n\nNew message to classify:\n{m.content}"

        try:
            from nanobot.ene.daemon.processor import DAEMON_PROMPT as _DAEMON_SYSTEM
        except ImportError:
            _DAEMON_SYSTEM = "(daemon system prompt unavailable)"

        loop._live.emit_prompt(
            "prompt_daemon", channel_key,
            sender=_sender_label,
            system=_DAEMON_SYSTEM,
            user=_daemon_user_msg,
        )
        logger.debug(f"Daemon prompt for {_sender_label}: {_daemon_user_msg[:300]}")

        # Call daemon
        daemon_result = await daemon_mod.process_message(
            content=m.content,
            sender_name=_sender_label,
            sender_id=caller_id,
            is_dad=is_dad,
            metadata=m.metadata,
            channel_state=channel_state,
            recent_context=_recent_context,
        )
        m.metadata["_daemon_result"] = daemon_result

        # Log daemon response
        loop._live.emit_prompt(
            "prompt_daemon_response", channel_key,
            sender=_sender_label,
            model=daemon_result.model_used,
            fallback=daemon_result.fallback_used,
            classification=daemon_result.classification.value,
            reason=daemon_result.classification_reason,
            confidence=daemon_result.confidence,
            topic=daemon_result.topic_summary,
            tone=daemon_result.emotional_tone,
            security_flags=", ".join(daemon_result.security_flags) if daemon_result.security_flags else None,
        )
        logger.debug(
            f"Daemon: {_sender_label} → {daemon_result.classification.value} "
            f"({'fallback' if daemon_result.fallback_used else daemon_result.model_used}) "
            f"[{daemon_result.classification_reason or 'no reason'}]"
        )

        # Live trace — daemon result (topic/tone/confidence for dashboard visibility)
        loop._live.emit(
            "daemon_result", channel_key,
            sender=_sender_label,
            classification=daemon_result.classification.value,
            reason=daemon_result.classification_reason,
            model=daemon_result.model_used,
            latency_ms=daemon_result.latency_ms,
            fallback=daemon_result.fallback_used,
            security_flags=", ".join(daemon_result.security_flags) if daemon_result.security_flags else None,
            topic=daemon_result.topic_summary,
            tone=daemon_result.emotional_tone,
            confidence=daemon_result.confidence,
        )

        # Auto-mute on high-severity security flags (never mute Dad)
        if daemon_result.should_auto_mute and not is_dad:
            sender_name = m.metadata.get("author_name", m.sender_id)
            logger.warning(f"Daemon: auto-muting {sender_name} (high severity security flag)")
            loop._muted_users[caller_id] = _time.time() + 1800  # 30 minutes
            loop._live.emit(
                "mute_event", channel_key,
                sender=sender_name,
                duration_min=30,
                reason="auto (security flags)",
            )
            return True  # Handled (dropped via mute)

        # Hard override: Ene mentioned or replied to → force RESPOND
        has_ene_signal = (
            bool(ENE_PATTERN.search(m.content))
            or m.metadata.get("is_reply_to_ene")
        )
        if has_ene_signal and daemon_result.classification.value != "respond":
            logger.debug(
                f"Daemon override: {daemon_result.classification.value} → respond "
                f"(Ene signal in message from {_sender_label})"
            )
            loop._live.emit(
                "classification", channel_key,
                sender=_sender_label,
                result="respond",
                source="daemon",
                override=f"ene_signal ({daemon_result.classification.value}→respond)",
            )
            respond_msgs.append(m)
        elif daemon_result.classification.value == "respond":
            # Staleness downgrade: stale + low confidence → CONTEXT
            if (
                m.metadata.get("_is_stale")
                and not is_dad
                and daemon_result.confidence < 0.85
            ):
                logger.debug(
                    f"Staleness downgrade: {_sender_label} "
                    f"RESPOND → CONTEXT (stale + confidence {daemon_result.confidence:.2f})"
                )
                loop._live.emit(
                    "classification", channel_key,
                    sender=_sender_label,
                    result="context",
                    source="daemon",
                    override=f"stale_downgrade (confidence={daemon_result.confidence:.2f})",
                )
                context_msgs.append(m)
            else:
                loop._live.emit(
                    "classification", channel_key,
                    sender=_sender_label,
                    result="respond",
                    source="daemon",
                )
                respond_msgs.append(m)
        elif daemon_result.classification.value == "drop" and not is_dad:
            loop._live.emit(
                "classification", channel_key,
                sender=_sender_label,
                result="drop",
                source="daemon",
            )
            # Silently dropped (safety: never drop Dad)
        else:
            loop._live.emit(
                "classification", channel_key,
                sender=_sender_label,
                result="context",
                source="daemon",
            )
            context_msgs.append(m)

        return True  # Message was classified by daemon

    # ── Stage 4: Lurk context-only batches ────────────────────────────

    def _lurk_context(
        self,
        channel_key: str,
        context_msgs: list[InboundMessage],
    ) -> None:
        """Feed lurked messages into conversation tracker without LLM call."""
        loop = self._loop
        _conv_mod = loop.module_registry.get_module("conversation_tracker")
        if _conv_mod and hasattr(_conv_mod, "tracker") and _conv_mod.tracker:
            _conv_mod.tracker.ingest_batch([], context_msgs, channel_key)
        logger.debug(
            f"Debounce: {len(context_msgs)} context-only messages in {channel_key}, lurked"
        )

    # ── Stage 5: Merge messages ───────────────────────────────────────

    def _merge_messages(
        self,
        channel_key: str,
        respond_msgs: list[InboundMessage],
        context_msgs: list[InboundMessage],
    ) -> InboundMessage:
        """Thread-aware merge (falls back to flat merge if tracker unavailable)."""
        loop = self._loop
        _conv_mod = loop.module_registry.get_module("conversation_tracker")
        if _conv_mod and hasattr(_conv_mod, "tracker") and _conv_mod.tracker:
            try:
                _conv_mod.tracker.ingest_batch(respond_msgs, context_msgs, channel_key)
                merged = _conv_mod.tracker.build_context(
                    respond_msgs, context_msgs, channel_key,
                    format_author_fn=loop._format_author,
                )
                # Scene Brief: extract batch participant IDs
                participant_ids = _conv_mod.tracker.get_batch_participant_ids(
                    respond_msgs, context_msgs, channel_key,
                )
                loop.module_registry.set_scene_participants(participant_ids)
                return merged
            except Exception as e:
                logger.error(f"Conversation tracker failed, falling back to flat merge: {e}")
                return loop._merge_messages_tiered(respond_msgs, context_msgs)
        else:
            return loop._merge_messages_tiered(respond_msgs, context_msgs)

    # ── Stage 6: Dispatch to LLM ─────────────────────────────────────

    async def _dispatch(
        self,
        channel_key: str,
        merged: InboundMessage,
        respond_msgs: list[InboundMessage],
        context_msgs: list[InboundMessage],
    ) -> None:
        """Route to single-thread or multi-thread LLM dispatch."""
        loop = self._loop
        _conv_mod = loop.module_registry.get_module("conversation_tracker")

        # Check for multiple respond threads
        respond_threads = []
        if _conv_mod and hasattr(_conv_mod, "tracker") and _conv_mod.tracker:
            respond_threads = _conv_mod.tracker.get_respond_threads(channel_key)

        try:
            if len(respond_threads) > 1:
                await self._dispatch_multi_thread(
                    channel_key, merged, _conv_mod, respond_threads,
                )
            else:
                await self._dispatch_single(channel_key, merged)

        except Exception as e:
            logger.error(f"Error processing batch: {e}", exc_info=True)
            loop._live.emit(
                "error", channel_key,
                stage="process_batch",
                error_message=str(e)[:200],
            )
            caller_id = f"{merged.channel}:{merged.sender_id}"
            if caller_id in DAD_IDS:
                await loop.bus.publish_outbound(OutboundMessage(
                    channel=merged.channel,
                    chat_id=merged.chat_id,
                    content=f"something broke: {str(e)[:200]}"
                ))
        finally:
            loop._live.update_state(processing=None, active_batch=None)
            loop.module_registry.clear_scene_participants()

    async def _dispatch_single(
        self,
        channel_key: str,
        merged: InboundMessage,
    ) -> None:
        """Single-thread path: one LLM call for the whole batch."""
        loop = self._loop
        response = await loop._process_message(merged)
        if response:
            loop._live.emit(
                "response_sent", channel_key,
                content_preview=response.content[:120] if response.content else "",
                reply_to=response.reply_to,
            )
            await loop.bus.publish_outbound(response)

    async def _dispatch_multi_thread(
        self,
        channel_key: str,
        merged: InboundMessage,
        conv_mod: Any,
        respond_threads: list[Any],
    ) -> None:
        """Multi-thread path: one LLM call per thread (capped at THREAD_CAP)."""
        loop = self._loop
        from nanobot.ene.conversation.formatter import build_single_thread_context

        threads_to_process = respond_threads[:_THREAD_CAP]

        for thread in threads_to_process:
            try:
                # Build focused context for this thread
                thread_content, thread_msg_id_map, primary_name = (
                    build_single_thread_context(
                        focus_thread=thread,
                        all_threads=conv_mod.tracker._threads,
                        pending=conv_mod.tracker._pending,
                        channel_key=channel_key,
                    )
                )

                if not thread_content.strip():
                    continue

                # Set focus target for this thread's LLM call
                topic = " ".join(thread.topic_keywords[:3]) if thread.topic_keywords else None
                conv_mod.set_focus_target(primary_name or "someone", topic)

                # Build focused InboundMessage for this thread
                focused_msg = self._build_thread_message(
                    merged, thread, thread_content, thread_msg_id_map,
                )

                response = await loop._process_message(focused_msg)
                if response:
                    loop._live.emit(
                        "response_sent", channel_key,
                        content_preview=response.content[:120] if response.content else "",
                        reply_to=response.reply_to,
                        thread_id=thread.thread_id[:8],
                    )
                    await loop.bus.publish_outbound(response)

                # Mark thread as responded + commit shown index
                conv_mod.tracker.mark_thread_responded(thread.thread_id)
                conv_mod.tracker.commit_shown_indices(
                    {thread.thread_id: len(thread.messages)}
                )

            except Exception as e:
                logger.error(
                    f"Error processing thread {thread.thread_id[:8]}: {e}",
                    exc_info=True,
                )
            finally:
                conv_mod.clear_focus_target()

        if len(respond_threads) > _THREAD_CAP:
            deferred = len(respond_threads) - _THREAD_CAP
            logger.info(
                f"Per-thread loop: processed {_THREAD_CAP} threads, "
                f"deferred {deferred} to next batch cycle"
            )

    def _build_thread_message(
        self,
        merged: InboundMessage,
        thread: Any,
        thread_content: str,
        thread_msg_id_map: dict,
    ) -> InboundMessage:
        """Build a focused InboundMessage for a single thread."""
        new_msgs = thread.messages[thread.last_shown_index:]
        thread_sender_id = merged.sender_id
        thread_metadata = {**merged.metadata}

        if new_msgs:
            # Use the most recent non-Ene sender from the thread
            for tm in reversed(new_msgs):
                if not tm.is_ene:
                    parts = tm.author_id.split(":", 1)
                    if len(parts) == 2:
                        thread_sender_id = parts[1]
                    thread_metadata["author_name"] = tm.author_name
                    thread_metadata["username"] = tm.author_username
                    # Fix reply targeting: point message_id to this thread's
                    # last non-Ene message, not the original batch trigger
                    if tm.discord_msg_id:
                        thread_metadata["message_id"] = tm.discord_msg_id
                    break

        thread_metadata["msg_id_map"] = thread_msg_id_map
        thread_metadata["thread_count"] = 1
        thread_metadata["debounce_count"] = len(new_msgs)
        thread_metadata["message_ids"] = [
            tm.discord_msg_id for tm in new_msgs if tm.discord_msg_id
        ]

        return InboundMessage(
            channel=merged.channel,
            sender_id=thread_sender_id,
            chat_id=merged.chat_id,
            content=thread_content,
            timestamp=merged.timestamp,
            metadata=thread_metadata,
        )
