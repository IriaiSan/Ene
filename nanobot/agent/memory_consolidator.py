"""Memory consolidation — diary entries, running summaries, re-anchoring.

Extracted from AgentLoop (Phase 5 refactor).
Three responsibilities:
    1. Diary consolidation: summarize old messages into diary entries
    2. Running summaries: recursive summarization for hybrid history
    3. Re-anchoring: periodic identity injection to prevent persona drift

All state lives on AgentLoop — MemoryConsolidator accesses it through
a back-reference (self._loop).
"""

from __future__ import annotations

import re
import time as _time
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.loop import AgentLoop
    from nanobot.session.manager import Session


class MemoryConsolidator:
    """Diary consolidation, running summaries, and re-anchoring.

    Handles the memory lifecycle: conversation → summary → diary entry.
    """

    def __init__(self, loop: "AgentLoop") -> None:
        self._loop = loop

    # ── Running summaries ─────────────────────────────────────────────

    async def generate_running_summary(
        self,
        session: "Session",
        key: str,
    ) -> str | None:
        """Generate or update a running summary of older conversation messages.

        Uses recursive summarization: summarize the old summary + new messages
        into a fresh summary. This keeps context compact while preserving
        important information. (Wang et al. 2023, MemGPT pattern)

        Only called when session has enough messages to warrant summarization.
        """
        loop = self._loop
        recent_count = 12

        if len(session.messages) <= recent_count:
            return loop._session_summaries.get(key)

        # Throttle — only regenerate summary every 3 messages to avoid extra LLM calls
        counter = loop._summary_msg_counters.get(key, 0) + 1
        loop._summary_msg_counters[key] = counter
        if counter < 3 and key in loop._session_summaries:
            return loop._session_summaries[key]  # reuse cached summary
        loop._summary_msg_counters[key] = 0  # reset counter on regen

        # Messages that need summarizing: everything before the recent window
        older_messages = session.messages[:-recent_count]
        if not older_messages:
            return loop._session_summaries.get(key)

        # Structured speaker-tagged formatting (same as diary consolidation)
        author_re = re.compile(r'(?:^|\n)(.+?) \(@(\w+)\): ', re.MULTILINE)
        lines = []
        for m in older_messages[-40:]:  # Cap at 40 messages to avoid huge prompts
            content = m.get("content", "")
            if not content:
                continue
            if m["role"] == "assistant":
                lines.append(f"[Ene]: {content[:300]}")
            else:
                # Parse author from merged message format
                matches = list(author_re.finditer(content))
                if matches:
                    for i, match in enumerate(matches):
                        display, username = match.group(1), match.group(2)
                        start = match.end()
                        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
                        text = content[start:end].strip()
                        lines.append(f"[{display} @{username}]: {text[:300]}")
                else:
                    display = m.get("author_name") or "Someone"
                    lines.append(f"[{display}]: {content[:300]}")
        if not lines:
            return loop._session_summaries.get(key)

        older_text = "\n".join(lines)

        existing_summary = loop._session_summaries.get(key, "")
        if existing_summary:
            prompt = loop._prompts.load(
                "summary_update",
                existing_summary=existing_summary,
                older_text=older_text,
            )
        else:
            prompt = loop._prompts.load("summary_new", older_text=older_text)

        try:
            model = loop.consolidation_model or loop.model
            _obs_start = _time.perf_counter()
            response = await loop.provider.chat(
                messages=[
                    {"role": "system", "content": loop._prompts.load("summary_system")},
                    {"role": "user", "content": prompt},
                ],
                model=model,
            )
            if loop._observatory:
                loop._observatory.record(
                    response, call_type="summary", model=model,
                    caller_id="system", latency_start=_obs_start,
                )
            summary = (response.content or "").strip()
            if summary:
                # Strip markdown fences
                if summary.startswith("```"):
                    summary = summary.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                loop._session_summaries[key] = summary
                logger.debug(f"Updated running summary for {key}: {summary[:80]}")
                return summary
        except Exception as e:
            logger.warning(f"Failed to generate running summary: {e}")

        return loop._session_summaries.get(key)

    # ── Re-anchoring ──────────────────────────────────────────────────

    def should_reanchor(self, session: "Session") -> bool:
        """Check if identity re-anchoring is needed to prevent persona drift.

        Research (DeepSeek v3.2 documented): persona drift starts at 8-12 turns.
        We inject a brief identity reminder every N assistant messages.
        """
        assistant_count = sum(1 for m in session.messages if m.get("role") == "assistant")
        return assistant_count > 0 and assistant_count % self._loop._reanchor_interval == 0

    # ── Diary consolidation ───────────────────────────────────────────

    async def consolidate(
        self,
        session: "Session",
        archive_all: bool = False,
    ) -> None:
        """Consolidate old messages into a diary entry.

        New memory architecture:
        - Interaction logs: written in real-time by _process_message (not here)
        - Diary entries: summarized here from the conversation buffer
        - Core memory: written by Ene via save_memory tool (not here)

        Args:
            session: The session to consolidate.
            archive_all: If True, summarize all messages (for /new command).
        """
        loop = self._loop

        if archive_all:
            old_messages = session.messages
            keep_count = 0
            logger.info(f"Diary consolidation (archive_all): {len(session.messages)} messages")
        else:
            keep_count = loop.memory_window // 2
            if len(session.messages) <= keep_count:
                return

            messages_to_process = len(session.messages) - session.last_consolidated
            if messages_to_process <= 0:
                return

            old_messages = session.messages[session.last_consolidated:-keep_count]
            if not old_messages:
                return
            logger.info(f"Diary consolidation: {len(old_messages)} messages to summarize")

        # Structured speaker-tagged message formatting
        # Research: CONFIT (NAACL 2022) shows ~45% of summarization errors are
        # wrong-speaker attribution. NexusSum (ACL 2025) shows 3rd-person
        # preprocessing gives 30% BERTScore improvement. We parse messages into
        # explicit [Speaker @handle]: content format so the diary LLM can't
        # confuse who said what.
        multi_author_re = re.compile(r'(?:^|\n)(.+?) \(@(\w+)\): ', re.MULTILINE)
        lines = []
        participants = set()
        last_timestamp = "?"

        for m in old_messages:
            content = m.get("content", "")
            if not content:
                continue
            ts = m.get("timestamp", "?")[:16]
            last_timestamp = ts

            if m["role"] == "assistant":
                lines.append(f"[{ts}] [Ene]: {content[:300]}")
                participants.add("Ene")
            else:
                # Check if this is a merged multi-sender message
                author_matches = list(multi_author_re.finditer(content))
                if len(author_matches) > 1:
                    # Multi-sender merged message — split into individual lines
                    for i, match in enumerate(author_matches):
                        display, username = match.group(1), match.group(2)
                        start = match.end()
                        end = author_matches[i + 1].start() if i + 1 < len(author_matches) else len(content)
                        text = content[start:end].strip()
                        lines.append(f"[{ts}] [{display} @{username}]: {text[:300]}")
                        participants.add(f"{display} @{username}")
                elif author_matches:
                    # Single "Author (@user): content" format
                    match = author_matches[0]
                    display, username = match.group(1), match.group(2)
                    text = content[match.end():].strip()
                    lines.append(f"[{ts}] [{display} @{username}]: {text[:300]}")
                    participants.add(f"{display} @{username}")
                else:
                    # No author prefix — use metadata (single sender, no merge)
                    display = m.get("author_name") or "Someone"
                    username = m.get("username") or ""
                    tag = f"{display} @{username}" if username else display
                    lines.append(f"[{ts}] [{tag}]: {content[:300]}")
                    participants.add(tag)

        # Strip #msg tags that leaked from thread-formatted session content
        _msg_tag_re = re.compile(r'#msg\d+\s+')
        participants = {_msg_tag_re.sub('', p).strip() for p in participants}
        participants = {p for p in participants if p}  # Remove empties
        conversation = _msg_tag_re.sub('', "\n".join(lines))

        if not conversation.strip():
            if not archive_all:
                session.last_consolidated = len(session.messages) - keep_count
            return

        # Build participant roster — unambiguous reference for the LLM
        roster_lines = ["- Ene — the diary author"]
        for p in sorted(participants - {"Ene"}):
            roster_lines.append(f"- {p}")
        roster = "\n".join(roster_lines)
        participant_list = ",".join(sorted(participants - {"Ene"}))

        prompt = loop._prompts.load(
            "diary_user", roster=roster, conversation=conversation,
        )

        # Use configurable consolidation model (falls back to main model)
        model = loop.consolidation_model or loop.model
        max_retries = 2

        # 3rd-person diary with explicit speaker attribution rules
        # (DS-SS extract-then-generate pattern, PLOS ONE 2024)
        diary_system_prompt = loop._prompts.load("diary_system")

        for attempt in range(max_retries + 1):
            try:
                _obs_start = _time.perf_counter()
                response = await loop.provider.chat(
                    messages=[
                        {"role": "system", "content": diary_system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    model=model,
                )
                if loop._observatory:
                    loop._observatory.record(
                        response, call_type="diary", model=model,
                        caller_id="system", latency_start=_obs_start,
                    )
                text = (response.content or "").strip()
                if not text:
                    logger.warning("Diary consolidation: empty response, skipping")
                    break

                # Strip any markdown fences the model might add
                if text.startswith("```"):
                    text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

                # Prepend structured metadata header for robust retrieval
                ts_short = last_timestamp[11:16] if len(last_timestamp) > 11 else last_timestamp
                entry = f"[{ts_short}] participants={participant_list}\n{text}"
                loop.memory.append_diary(entry)
                logger.info(f"Diary entry written: {text[:80]}")

                if archive_all:
                    session.last_consolidated = 0
                else:
                    session.last_consolidated = len(session.messages) - keep_count
                return  # success

            except Exception as e:
                if attempt < max_retries:
                    # Drop oldest messages and retry
                    old_messages = old_messages[10:]
                    if not old_messages:
                        logger.warning("Diary consolidation: no messages left after truncation")
                        break
                    # Rebuild with same structured format
                    lines = []
                    for m in old_messages:
                        if not m.get("content"):
                            continue
                        ts = m.get("timestamp", "?")[:16]
                        if m["role"] == "assistant":
                            lines.append(f"[{ts}] [Ene]: {m['content'][:300]}")
                        else:
                            lines.append(f"[{ts}] {m['content'][:300]}")
                    conversation = "\n".join(lines)
                    prompt = loop._prompts.load(
                        "diary_fallback", conversation=conversation,
                    )
                    logger.warning(f"Diary consolidation attempt {attempt+1} failed ({e}), retrying")
                else:
                    logger.error(f"Diary consolidation failed after {max_retries+1} attempts: {e}")
                    if not archive_all:
                        session.last_consolidated = len(session.messages) - keep_count
