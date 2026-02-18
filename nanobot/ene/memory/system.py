"""MemorySystem — facade coordinating core memory, vector memory, and diary.

This is the single entry point for all memory operations. The MemoryModule
and all tools reference this facade rather than individual components.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.ene.memory.core_memory import CoreMemory
from nanobot.ene.memory.vector_memory import VectorMemory

if TYPE_CHECKING:
    from nanobot.ene.memory.embeddings import EneEmbeddings


class MemorySystem:
    """Facade coordinating all memory subsystems.

    Usage:
        system = MemorySystem(workspace_path, embedding_fn=embedder.embed)
        system.initialize()

        # Tools reference system.core and system.vector
        tool = SaveMemoryTool(system)
    """

    def __init__(
        self,
        workspace: Path,
        token_budget: int = 4000,
        chroma_path: str | None = None,
        embedding_fn: Any = None,
        diary_context_days: int = 3,
    ):
        self._workspace = workspace
        self._memory_dir = workspace / "memory"
        self._diary_dir = workspace / "memory" / "diary"
        self._token_budget = token_budget
        self._chroma_path = chroma_path or str(workspace / "chroma_db")
        self._embedding_fn = embedding_fn
        self._diary_context_days = diary_context_days

        # Entity name cache (refreshed when entities change)
        self._entity_cache: dict[str, str] = {}
        self._entity_cache_dirty = True

        # Initialize components
        self._core: CoreMemory | None = None
        self._vector: VectorMemory | None = None

    def initialize(self) -> None:
        """Initialize all memory subsystems."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        self._diary_dir.mkdir(parents=True, exist_ok=True)

        # Core memory (always available)
        self._core = CoreMemory(self._memory_dir, token_budget=self._token_budget)

        # Vector memory
        try:
            self._vector = VectorMemory(
                chroma_path=self._chroma_path,
                embedding_fn=self._embedding_fn,
            )
            logger.info("Vector memory initialized")
        except Exception as e:
            logger.error(f"Failed to initialize vector memory: {e}")
            self._vector = None

        # Run migration if needed
        self._maybe_migrate()

    @property
    def core(self) -> CoreMemory:
        """Core memory (structured, token-budgeted, always in context)."""
        if self._core is None:
            raise RuntimeError("MemorySystem not initialized — call initialize() first")
        return self._core

    @property
    def vector(self) -> VectorMemory | None:
        """Vector memory (long-term, searchable). May be None if initialization failed."""
        return self._vector

    # ── Context for System Prompt ──────────────────────────

    def get_memory_context(self) -> str:
        """Build the memory context block for the system prompt.

        Returns core memory + recent diary entries formatted as markdown.
        """
        parts: list[str] = []

        # Core memory
        if self._core:
            parts.append(self._core.render_for_context())

        # Recent diary entries
        diary_text = self._load_recent_diary()
        if diary_text:
            parts.append(f"## Recent Diary\n{diary_text}")

        return "\n\n".join(parts)

    def get_relevant_context(self, message: str) -> str:
        """Get retrieval-augmented context for a specific message.

        Searches vector memory and checks entity keywords.
        Returns formatted markdown or empty string if nothing relevant.
        """
        if not self._vector:
            return ""

        parts: list[str] = []

        # Search vector memory
        try:
            results = self._vector.search(message, limit=5)
            if results:
                lines = ["## Retrieved Memories"]
                for r in results:
                    lines.append(f"- [{r.memory_type}] {r.content}")
                parts.append("\n".join(lines))
        except Exception as e:
            logger.error(f"Memory retrieval failed: {e}")

        # Check entity keywords in message
        try:
            entity_context = self._get_entity_context(message)
            if entity_context:
                parts.append(entity_context)
        except Exception as e:
            logger.error(f"Entity context failed: {e}")

        return "\n\n".join(parts)

    def _get_entity_context(self, message: str) -> str:
        """Check if any known entity names appear in the message.

        If found, inject their profiles into context.
        """
        if not self._vector:
            return ""

        # Refresh cache if needed
        if self._entity_cache_dirty:
            self._entity_cache = self._vector.get_entity_names()
            self._entity_cache_dirty = False

        message_lower = message.lower()
        matched_ids: set[str] = set()

        for name, eid in self._entity_cache.items():
            if name in message_lower:
                matched_ids.add(eid)

        if not matched_ids:
            return ""

        lines = ["## Entity Context"]
        for eid in matched_ids:
            results = self._vector.search_entities(eid, limit=1)
            if results:
                er = results[0]
                lines.append(f"- **{er.name}** ({er.entity_type}): {er.description}")

        return "\n".join(lines) if len(lines) > 1 else ""

    def invalidate_entity_cache(self) -> None:
        """Mark entity cache as dirty (call after entity changes)."""
        self._entity_cache_dirty = True

    # ── Diary ──────────────────────────────────────────────

    def _load_recent_diary(self) -> str:
        """Load the last N days of diary entries, capped at MAX_DIARY_ENTRIES.

        Strips participant= metadata lines (useful for search, not for LLM context)
        and caps total entries to avoid context bloat from busy servers.
        """
        import re
        from collections import OrderedDict
        from datetime import datetime, timedelta

        MAX_DIARY_ENTRIES = 7  # Hard cap on entries in system prompt

        if not self._diary_dir.exists():
            return ""

        # Collect individual entries across days (most recent day first)
        all_entries: list[tuple[str, str]] = []  # (day_str, entry_text)
        today = datetime.now().date()

        for days_ago in range(self._diary_context_days):
            day = today - timedelta(days=days_ago)
            diary_file = self._diary_dir / f"{day.isoformat()}.md"
            if diary_file.exists():
                try:
                    content = diary_file.read_text(encoding="utf-8").strip()
                    if content:
                        # Strip participant= metadata — useful for search, not for LLM
                        content = re.sub(
                            r'^\[[\d:]+\] participants=.*$', '',
                            content, flags=re.MULTILINE,
                        )
                        content = re.sub(r'\n{3,}', '\n\n', content).strip()
                        if content:
                            # Split into individual entries (separated by blank lines)
                            day_entries = [
                                e.strip() for e in content.split('\n\n') if e.strip()
                            ]
                            for entry in day_entries:
                                all_entries.append((day.isoformat(), entry))
                except Exception as e:
                    logger.error(f"Failed to read diary {diary_file}: {e}")

        if not all_entries:
            return ""

        # Keep only the last MAX_DIARY_ENTRIES entries (most recent)
        capped = all_entries[-MAX_DIARY_ENTRIES:]

        # Group back by day for display
        by_day: OrderedDict[str, list[str]] = OrderedDict()
        for day_str, entry in capped:
            by_day.setdefault(day_str, []).append(entry)

        parts = []
        for day_str, day_entries in by_day.items():
            parts.append(f"### {day_str}\n" + "\n\n".join(day_entries))

        return "\n\n".join(parts)

    def write_diary_entry(self, content: str) -> None:
        """Append a diary entry for today."""
        from datetime import datetime

        self._diary_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().date().isoformat()
        diary_file = self._diary_dir / f"{today}.md"

        with open(diary_file, "a", encoding="utf-8") as f:
            f.write(f"\n{content}\n")

        logger.debug(f"Wrote diary entry to {diary_file}")

    # ── Migration ──────────────────────────────────────────

    def _maybe_migrate(self) -> None:
        """Run one-time migration from v1 memory format if needed.

        Migration is triggered when core.json doesn't exist but
        legacy memory files (MEMORY.md or CORE.md) do.
        """
        core_json = self._memory_dir / "core.json"
        if core_json.exists():
            return  # Already migrated

        # Check for legacy files
        legacy_files = [
            self._workspace / "MEMORY.md",
            self._workspace / "CORE.md",
            self._memory_dir / "MEMORY.md",
            self._memory_dir / "CORE.md",
        ]

        found_legacy = None
        for f in legacy_files:
            if f.exists():
                found_legacy = f
                break

        if found_legacy is None:
            return  # Nothing to migrate

        logger.info(f"Migrating from legacy memory file: {found_legacy}")
        try:
            self._run_migration(found_legacy)
        except Exception as e:
            logger.error(f"Migration failed: {e}", exc_info=True)

    def _run_migration(self, legacy_file: Path) -> None:
        """Migrate a legacy memory file to core.json + vector store."""
        content = legacy_file.read_text(encoding="utf-8")
        lines = content.strip().split("\n")

        # Parse entries from the legacy format
        entries: list[dict[str, Any]] = []
        current_section = "scratch"

        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                # Detect section headers
                lower = line.lower()
                if "identity" in lower or "who i am" in lower:
                    current_section = "identity"
                elif "people" in lower or "person" in lower:
                    current_section = "people"
                elif "preference" in lower or "rule" in lower:
                    current_section = "preferences"
                elif "context" in lower or "current" in lower:
                    current_section = "context"
                continue
            # Strip leading bullet/dash
            if line.startswith("- ") or line.startswith("* "):
                line = line[2:]

            entries.append({
                "content": line,
                "section": current_section,
                "importance": 5,
            })

        if not entries:
            return

        # Add entries to core memory (up to budget)
        core = self.core
        added = 0
        archived = 0

        for entry in entries:
            entry_id = core.add_entry(
                entry["section"],
                entry["content"],
                importance=entry["importance"],
            )
            if entry_id:
                added += 1
            elif self._vector:
                # Over budget — archive to vector store
                self._vector.add_memory(
                    content=entry["content"],
                    memory_type="archived_core",
                    importance=entry["importance"],
                    source="migration",
                )
                archived += 1

        # Backup the legacy file
        backup = legacy_file.with_suffix(legacy_file.suffix + ".bak")
        legacy_file.rename(backup)

        logger.info(
            f"Migration complete: {added} entries to core, "
            f"{archived} archived to vector store. "
            f"Legacy file backed up to {backup}"
        )

        # Index existing diary files
        self._index_diary_files()

    def _index_diary_files(self) -> None:
        """Index existing diary files into vector store."""
        if not self._vector or not self._diary_dir.exists():
            return

        count = 0
        for diary_file in sorted(self._diary_dir.glob("*.md")):
            try:
                content = diary_file.read_text(encoding="utf-8").strip()
                if content:
                    date_str = diary_file.stem
                    self._vector.add_memory(
                        content=f"[Diary {date_str}] {content[:500]}",
                        memory_type="diary",
                        importance=4,
                        source="diary_migration",
                    )
                    count += 1
            except Exception as e:
                logger.error(f"Failed to index diary {diary_file}: {e}")

        if count:
            logger.info(f"Indexed {count} diary files into vector store")
