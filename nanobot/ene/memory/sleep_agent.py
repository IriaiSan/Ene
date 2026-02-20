"""SleepTimeAgent — Ene's subconscious background memory processor.

Runs in two modes:
- **Quick (idle)**: Triggered after 5 min idle. Extracts facts and entities
  from recent conversations, indexes to vector store, writes diary.
- **Deep (daily)**: Triggered at 4 AM. Generates reflections, detects
  contradictions, prunes weak memories, reviews core budget.

Uses a separate (often cheaper) model at low temperature for precision.
"""

from __future__ import annotations

import json
import re
import time as _time
from datetime import datetime
from typing import Any, TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from nanobot.ene.memory.system import MemorySystem
    from nanobot.ene.observatory.collector import MetricsCollector
    from nanobot.ene.observatory.module_metrics import ModuleMetrics
    from nanobot.providers.base import LLMProvider

# Module-level metrics instance — set by set_metrics() during init.
_metrics: "ModuleMetrics | None" = None


def set_metrics(metrics: "ModuleMetrics") -> None:
    """Attach a ModuleMetrics instance for memory observability."""
    global _metrics
    _metrics = metrics


# ── Prompts ────────────────────────────────────────────────

EXTRACT_FACTS_PROMPT = (
    "You are Ene's memory processor. Analyze the following conversation and extract:\n"
    "1. New facts worth remembering (things learned, preferences stated, events)\n"
    "2. Entities mentioned (people, places, projects, organizations)\n\n"
    "Return ONLY valid JSON in this format:\n"
    '{"facts": [{"content": "...", "importance": 1-10, "related_entities": "comma,separated"}], '
    '"entities": [{"name": "...", "type": "person|place|project|organization|other", "description": "...", "importance": 1-10}]}\n\n'
    "Rules:\n"
    "- Importance 1-3: trivial/temporary. 4-6: useful. 7-9: important. 10: permanent/identity.\n"
    "- Only extract genuinely new information, not greetings or filler.\n"
    '- If nothing worth remembering, return {"facts": [], "entities": []}.\n'
    "- Be concise. Each fact should be one clear sentence.\n"
    "- Entity descriptions should capture the relationship to Ene.\n\n"
    "Conversation:\n"
)

CONTRADICTION_CHECK_PROMPT = (
    "You are checking if a new fact contradicts an existing memory.\n\n"
    "Existing memory: %s\n"
    "New fact: %s\n\n"
    "Do these contradict each other? If so, which is more likely to be current/correct?\n\n"
    'Return ONLY valid JSON: {"contradicts": true/false, "keep": "existing" or "new", "reason": "brief explanation"}\n'
)

REFLECTION_PROMPT = (
    "You are Ene's reflective thinking process. Based on these recent memories,\n"
    "generate 1-3 higher-level insights or patterns you notice.\n\n"
    "Recent memories:\n%s\n\n"
    'Return ONLY valid JSON: {"reflections": [{"content": "...", "importance": 1-10, "topic": "brief_topic_label"}]}\n\n'
    "Rules:\n"
    "- Reflections should be about patterns, themes, or insights — not just restating facts.\n"
    '- If no meaningful patterns emerge, return {"reflections": []}.\n'
    "- Be concise. Each reflection is one sentence.\n"
)

PRUNING_PROMPT = (
    "You are reviewing weak memories for potential pruning. For each memory,\n"
    "decide if it should be kept or pruned.\n\n"
    "Memories to review:\n%s\n\n"
    'Return ONLY valid JSON: {"decisions": [{"id": "memory_id", "action": "keep" or "prune", "reason": "brief reason"}]}\n\n'
    "Rules:\n"
    "- Prune trivial, outdated, or redundant information.\n"
    "- Keep anything that might be useful later or has sentimental value.\n"
    "- When in doubt, keep.\n"
)

CORE_REVIEW_PROMPT = (
    "You are reviewing Ene's core memory for budget management. Core memory is\n"
    "over budget by %s tokens.\n\n"
    "Current entries:\n%s\n\n"
    "Suggest entries to archive (move to searchable long-term memory) to free\n"
    "up space. Prioritize keeping high-importance and frequently-referenced entries.\n\n"
    'Return ONLY valid JSON: {"archive": [{"id": "entry_id", "reason": "brief reason for archiving"}]}\n'
)


class SleepTimeAgent:
    """Background memory processor — Ene's subconscious.

    Processes conversations into structured memories, tracks entities,
    detects contradictions, generates reflections, and prunes weak memories.
    """

    def __init__(
        self,
        system: "MemorySystem",
        provider: "LLMProvider",
        model: str | None = None,
        temperature: float = 0.3,
        observatory: "MetricsCollector | None" = None,
    ):
        self._system = system
        self._provider = provider
        self._model = model
        self._temperature = temperature
        self._observatory = observatory

    async def _llm_call(self, prompt: str) -> str:
        """Make a single LLM call and return the response content."""
        messages = [{"role": "user", "content": prompt}]

        _obs_start = _time.perf_counter()
        response = await self._provider.chat(
            messages=messages,
            model=self._model,
            max_tokens=2048,
            temperature=self._temperature,
        )
        if self._observatory:
            self._observatory.record(
                response, call_type="sleep", model=self._model or "unknown",
                caller_id="system", latency_start=_obs_start,
            )

        return response.content or ""

    def _parse_json(self, text: str) -> dict | None:
        """Parse JSON from LLM response, handling common issues."""
        if not text:
            return None

        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try extracting JSON from markdown code blocks
        json_match = re.search(r"```(?:json)?\s*([\s\S]*?)```", text)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try finding JSON object in text
        brace_match = re.search(r"\{[\s\S]*\}", text)
        if brace_match:
            try:
                return json.loads(brace_match.group())
            except json.JSONDecodeError:
                pass

        # Try json_repair if available
        try:
            import json_repair
            return json_repair.loads(text)
        except (ImportError, Exception):
            pass

        logger.warning(f"Failed to parse JSON from LLM response: {text[:200]}")
        return None

    # ── Quick Path (Idle Processing) ──────────────────────

    async def process_idle(
        self,
        conversation_text: str | None = None,
    ) -> dict[str, int]:
        """Quick processing after idle period.

        1. Extract facts and entities from recent conversation
        2. Check for contradictions with existing memories
        3. Index new facts to vector store
        4. Update entities
        5. Write diary entry

        Args:
            conversation_text: Recent conversation to process. If None,
                will attempt to build from recent session logs.

        Returns:
            Stats dict: {"facts_added": N, "entities_updated": N}
        """
        stats = {"facts_added": 0, "entities_updated": 0}

        if not conversation_text:
            logger.debug("No conversation text for idle processing, skipping")
            return stats

        # Step 1: Extract facts and entities
        facts_data = await self._extract_facts_and_entities(conversation_text)
        if not facts_data:
            return stats

        # Record extraction metrics
        _facts_list = facts_data.get("facts", [])
        _ent_list = facts_data.get("entities", [])
        if _metrics and (_facts_list or _ent_list):
            _metrics.record(
                "facts_extracted",
                count=len(_facts_list),
                entity_count=len(_ent_list),
                entities=[e.get("name", "") for e in _ent_list[:10]],
                importance_scores=[f.get("importance", 5) for f in _facts_list],
            )

        # Step 2-3: Process facts (with contradiction checking)
        facts = facts_data.get("facts", [])
        for fact in facts:
            content = fact.get("content", "").strip()
            if not content:
                continue

            importance = max(1, min(10, fact.get("importance", 5)))
            related = fact.get("related_entities", "")

            # Check for contradictions
            await self._check_and_handle_contradiction(content, importance)

            # Add to vector store
            if self._system.vector:
                self._system.vector.add_memory(
                    content=content,
                    memory_type="fact",
                    importance=importance,
                    source="sleep_agent_idle",
                    related_entities=related,
                )
                stats["facts_added"] += 1

        # Step 4: Process entities
        entities = facts_data.get("entities", [])
        for entity in entities:
            name = entity.get("name", "").strip()
            if not name:
                continue

            if self._system.vector:
                self._system.vector.upsert_entity(
                    name=name,
                    entity_type=entity.get("type", "other"),
                    description=entity.get("description", ""),
                    importance=max(1, min(10, entity.get("importance", 5))),
                )
                stats["entities_updated"] += 1
                self._system.invalidate_entity_cache()

        # Step 5: Write diary entry
        if stats["facts_added"] > 0:
            diary_content = self._build_diary_entry(facts, entities)
            self._system.write_diary_entry(diary_content)
            if _metrics:
                _metrics.record(
                    "diary_written",
                    entry_length=len(diary_content),
                    facts_count=len(facts),
                    entity_count=len(entities),
                    model_used=self._model or "unknown",
                    path="idle",
                )

        logger.info(
            f"Idle processing complete: "
            f"{stats['facts_added']} facts, "
            f"{stats['entities_updated']} entities"
        )
        return stats

    async def _extract_facts_and_entities(self, text: str) -> dict | None:
        """Extract facts and entities from conversation text."""
        prompt = EXTRACT_FACTS_PROMPT + text[:4000]  # Truncate to fit context

        try:
            response = await self._llm_call(prompt)
            data = self._parse_json(response)
            if data and isinstance(data, dict):
                return data
        except Exception as e:
            logger.error(f"Fact extraction failed: {e}")

        return None

    async def _check_and_handle_contradiction(
        self,
        new_fact: str,
        importance: int,
    ) -> None:
        """Check if a new fact contradicts existing memories."""
        if not self._system.vector:
            return

        # Search for similar existing memories
        similar = self._system.vector.search(
            new_fact,
            memory_type="fact",
            limit=3,
            overfetch_factor=2,
        )

        if not similar:
            return

        # Check the top result for contradiction
        top = similar[0]
        if top.score < 0.5:  # Not similar enough to contradict
            return

        prompt = CONTRADICTION_CHECK_PROMPT % (top.content, new_fact)

        try:
            response = await self._llm_call(prompt)
            data = self._parse_json(response)

            if data and data.get("contradicts"):
                resolution = data.get("keep", "existing")
                if _metrics:
                    _metrics.record(
                        "contradiction_found",
                        existing_memory=top.content[:100],
                        new_fact=new_fact[:100],
                        resolution=resolution,
                        reason=data.get("reason", ""),
                    )
                if resolution == "new":
                    # Supersede the old memory
                    self._system.vector.mark_superseded(top.id, "new_fact")
                    logger.info(
                        f"Contradiction resolved: superseded [{top.id}] "
                        f"'{top.content[:50]}' with '{new_fact[:50]}'"
                    )
        except Exception as e:
            logger.error(f"Contradiction check failed: {e}")

    def _build_diary_entry(
        self,
        facts: list[dict],
        entities: list[dict],
    ) -> str:
        """Build a diary entry from extracted facts and entities."""
        now = datetime.now().strftime("%H:%M")
        lines = [f"**{now}** — Sleep agent processed idle conversation."]

        if facts:
            lines.append("Facts learned:")
            for f in facts[:5]:
                content = f.get("content", "")
                imp = f.get("importance", 5)
                lines.append(f"  - {content} (imp:{imp})")

        if entities:
            lines.append("Entities seen:")
            for e in entities[:5]:
                lines.append(f"  - {e.get('name', '?')} ({e.get('type', '?')})")

        return "\n".join(lines)

    # ── Deep Path (Daily Processing) ──────────────────────

    async def process_daily(self) -> dict[str, int]:
        """Deep processing on daily schedule.

        1. Generate reflections from recent memories
        2. Prune weak memories
        3. Review core memory budget

        Returns:
            Stats dict with processing counts.
        """
        stats = {
            "reflections_added": 0,
            "memories_pruned": 0,
            "core_entries_archived": 0,
        }

        if not self._system.vector:
            return stats

        # Step 1: Generate reflections
        try:
            reflections = await self._generate_reflections()
            stats["reflections_added"] = reflections
        except Exception as e:
            logger.error(f"Reflection generation failed: {e}")

        # Step 2: Prune weak memories
        try:
            pruned = await self._prune_weak_memories()
            stats["memories_pruned"] = pruned
        except Exception as e:
            logger.error(f"Memory pruning failed: {e}")

        # Step 3: Review core memory budget
        try:
            archived = await self._review_core_budget()
            stats["core_entries_archived"] = archived
        except Exception as e:
            logger.error(f"Core budget review failed: {e}")

        # Write diary entry about daily processing
        self._system.write_diary_entry(
            f"**04:00** — Daily deep processing: "
            f"{stats['reflections_added']} reflections, "
            f"{stats['memories_pruned']} pruned, "
            f"{stats['core_entries_archived']} core entries archived."
        )

        logger.info(f"Daily processing complete: {stats}")
        return stats

    async def _generate_reflections(self) -> int:
        """Generate higher-level reflections from recent memories."""
        if not self._system.vector:
            return 0

        # Get recent memories for reflection
        # Search with a broad query to get varied recent content
        results = self._system.vector.search(
            "recent events conversations facts",
            limit=20,
            overfetch_factor=2,
        )

        if len(results) < 3:  # Not enough to reflect on
            return 0

        memory_text = "\n".join(
            f"- [{r.memory_type}] {r.content}" for r in results[:15]
        )

        prompt = REFLECTION_PROMPT % memory_text

        try:
            response = await self._llm_call(prompt)
            data = self._parse_json(response)

            if not data or "reflections" not in data:
                return 0

            count = 0
            source_ids = ",".join(r.id for r in results[:5])

            for ref in data["reflections"]:
                content = ref.get("content", "").strip()
                if not content:
                    continue

                self._system.vector.add_reflection(
                    content=content,
                    importance=max(1, min(10, ref.get("importance", 5))),
                    source_ids=source_ids,
                    topic=ref.get("topic", ""),
                )
                count += 1
                if _metrics:
                    _metrics.record(
                        "reflection_generated",
                        topic=ref.get("topic", ""),
                        insight_length=len(content),
                        importance=ref.get("importance", 5),
                    )

            return count
        except Exception as e:
            logger.error(f"Reflection generation failed: {e}")
            return 0

    async def _prune_weak_memories(self) -> int:
        """Prune memories with low strength and low importance."""
        if not self._system.vector:
            return 0

        candidates = self._system.vector.get_pruning_candidates(
            decay_rate=0.1,
            prune_threshold=0.2,
            max_importance=4,
            limit=20,
        )

        if not candidates:
            return 0

        # Format for LLM review
        candidates_text = "\n".join(
            f"- id:{c['id']} | imp:{c['importance']} | "
            f"strength:{c['strength']} | accesses:{c['access_count']} | "
            f"content: {c['content'][:100]}"
            for c in candidates
        )

        prompt = PRUNING_PROMPT % candidates_text

        try:
            response = await self._llm_call(prompt)
            data = self._parse_json(response)

            if not data or "decisions" not in data:
                return 0

            pruned = 0
            kept = 0
            for decision in data["decisions"]:
                if decision.get("action") == "prune":
                    mid = decision.get("id", "")
                    if self._system.vector.delete_memory(mid):
                        pruned += 1
                        logger.debug(
                            f"Pruned memory [{mid}]: {decision.get('reason', '')}"
                        )
                else:
                    kept += 1

            if _metrics:
                _metrics.record(
                    "pruning_decision",
                    candidates_reviewed=len(candidates),
                    items_pruned=pruned,
                    items_kept=kept,
                )

            return pruned
        except Exception as e:
            logger.error(f"Pruning failed: {e}")
            return 0

    async def _review_core_budget(self) -> int:
        """Review core memory and archive if over budget."""
        core = self._system.core
        current_tokens = core.get_total_tokens()
        max_tokens = core.token_budget

        # Always record budget check
        if _metrics:
            _metrics.record(
                "budget_check",
                current_tokens=current_tokens,
                max_tokens=max_tokens,
                utilization_pct=round(current_tokens / max_tokens * 100, 1) if max_tokens else 0,
                over_budget=core.is_over_budget,
            )

        if not core.is_over_budget:
            return 0

        over_by = current_tokens - max_tokens
        entries = core.get_all_entries()

        entries_text = "\n".join(
            f"- id:{e['id']} | section:{sec} | imp:{e['importance']} | "
            f"content: {e['content'][:100]}"
            for sec, e in entries
        )

        prompt = CORE_REVIEW_PROMPT % (over_by, entries_text)

        try:
            response = await self._llm_call(prompt)
            data = self._parse_json(response)

            if not data or "archive" not in data:
                return 0

            archived = 0
            for item in data["archive"]:
                entry_id = item.get("id", "")
                deleted = core.delete_entry(entry_id)

                if deleted and self._system.vector:
                    self._system.vector.add_memory(
                        content=deleted["content"],
                        memory_type="archived_core",
                        importance=deleted.get("importance", 5),
                        source="core_budget_review",
                    )
                    archived += 1
                    logger.info(
                        f"Archived core entry [{entry_id}]: "
                        f"{item.get('reason', '')}"
                    )

            return archived
        except Exception as e:
            logger.error(f"Core budget review failed: {e}")
            return 0
