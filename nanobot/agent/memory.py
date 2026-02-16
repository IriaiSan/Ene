"""Ene's memory system — four-layer persistent memory architecture.

Layers:
1. Core Memory  — CORE.md, Ene's personal important memories (always in context)
2. Diary        — diary/YYYY-MM-DD.md, first-person daily journal entries
3. Interaction Logs — logs/YYYY-MM-DD/{channel}.md, detailed raw records
4. Short-term   — existing per-channel JSONL sessions (managed by SessionManager)
"""

from datetime import datetime, date
from pathlib import Path

from loguru import logger

from nanobot.utils.helpers import ensure_dir, safe_filename


class MemoryStore:
    """Manages Ene's four-layer memory system."""

    def __init__(self, workspace: Path, diary_context_days: int = 3):
        self.memory_dir = ensure_dir(workspace / "memory")
        self.core_file = self.memory_dir / "CORE.md"
        self.diary_dir = ensure_dir(self.memory_dir / "diary")
        self.logs_dir = ensure_dir(self.memory_dir / "logs")
        self.diary_context_days = diary_context_days

        # Legacy file paths (for migration)
        self._legacy_memory = self.memory_dir / "MEMORY.md"
        self._legacy_history = self.memory_dir / "HISTORY.md"

    # ── Core Memory ──────────────────────────────────────────

    def read_core(self) -> str:
        """Read CORE.md (Ene's permanent important memories)."""
        if self.core_file.exists():
            return self.core_file.read_text(encoding="utf-8")
        return ""

    def append_core(self, entry: str) -> None:
        """Append to CORE.md. Ene writes this herself via save_memory tool."""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
        formatted = f"\n[{timestamp}] {entry.strip()}\n"
        with open(self.core_file, "a", encoding="utf-8") as f:
            f.write(formatted)
        logger.info(f"Core memory saved: {entry[:80]}")

    # ── Diary ────────────────────────────────────────────────

    def _diary_path(self, d: date | None = None) -> Path:
        """Get diary file path for a given date (defaults to today)."""
        d = d or date.today()
        return self.diary_dir / f"{d.isoformat()}.md"

    def read_diary(self, d: date | None = None) -> str:
        """Read a diary entry for a specific date."""
        path = self._diary_path(d)
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    def append_diary(self, entry: str, d: date | None = None) -> None:
        """Append to today's diary. Written by consolidation process."""
        path = self._diary_path(d)
        timestamp = datetime.now().strftime("%H:%M")
        formatted = f"\n[{timestamp}] {entry.strip()}\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(formatted)

    def get_recent_diary_entries(self, count: int | None = None) -> str:
        """Load the most recent N diary entries for context injection.

        Returns formatted string with date headers, chronological order.
        """
        count = count or self.diary_context_days
        diary_files = sorted(self.diary_dir.glob("*.md"), reverse=True)[:count]
        if not diary_files:
            return ""

        parts = []
        for f in reversed(diary_files):  # chronological order (oldest first)
            day = f.stem  # YYYY-MM-DD
            content = f.read_text(encoding="utf-8").strip()
            if content:
                parts.append(f"### {day}\n{content}")

        return "\n\n".join(parts)

    # ── Interaction Logs ─────────────────────────────────────

    def _log_dir_for_date(self, d: date | None = None) -> Path:
        """Get the log directory for a given date."""
        d = d or date.today()
        return ensure_dir(self.logs_dir / d.isoformat())

    def _log_filename(self, session_key: str) -> str:
        """Convert session key (e.g., 'discord:1234') to safe log filename."""
        return safe_filename(session_key.replace(":", "_")) + ".md"

    def append_interaction_log(
        self,
        session_key: str,
        role: str,
        content: str,
        author_name: str | None = None,
        tools_used: list[str] | None = None,
        d: date | None = None,
    ) -> None:
        """Append a message to the interaction log. Called by Python, not LLM."""
        log_dir = self._log_dir_for_date(d)
        filename = self._log_filename(session_key)
        path = log_dir / filename

        timestamp = datetime.now().strftime("%H:%M:%S")
        tools_str = f" [tools: {', '.join(tools_used)}]" if tools_used else ""
        name = f" ({author_name})" if author_name else ""
        line = f"[{timestamp}] {role.upper()}{name}{tools_str}: {content}\n"

        with open(path, "a", encoding="utf-8") as f:
            f.write(line)

    def read_interaction_log(self, session_key: str, d: date | None = None) -> str:
        """Read an interaction log. Ene reads these on demand for detail."""
        log_dir = self._log_dir_for_date(d)
        filename = self._log_filename(session_key)
        path = log_dir / filename
        if path.exists():
            return path.read_text(encoding="utf-8")
        return ""

    # ── Context Assembly ─────────────────────────────────────

    def get_memory_context(self) -> str:
        """Build the memory block for injection into the system prompt.

        Includes: full CORE.md + recent diary entries.
        Interaction logs are NOT included (too large; read on demand).
        """
        parts = []

        core = self.read_core()
        if core:
            parts.append(f"## Core Memory\n{core}")

        diary = self.get_recent_diary_entries()
        if diary:
            parts.append(f"## Recent Diary\n{diary}")

        return "\n\n".join(parts)

    # ── Migration ────────────────────────────────────────────

    def migrate_legacy(self) -> bool:
        """One-time migration from MEMORY.md/HISTORY.md to new system.

        - MEMORY.md contents become the initial CORE.md
        - HISTORY.md contents become a diary entry
        - Legacy files renamed to .bak (not deleted)

        Returns True if migration happened, False if already done.
        """
        if self.core_file.exists():
            return False  # Already migrated

        migrated = False

        if self._legacy_memory.exists():
            content = self._legacy_memory.read_text(encoding="utf-8").strip()
            if content:
                header = "# Core Memory\n\n*Migrated from MEMORY.md*\n\n"
                self.core_file.write_text(header + content, encoding="utf-8")
                migrated = True
            self._legacy_memory.rename(self._legacy_memory.with_suffix(".md.bak"))
            logger.info("Migrated MEMORY.md -> CORE.md")

        if self._legacy_history.exists():
            content = self._legacy_history.read_text(encoding="utf-8").strip()
            if content:
                today = date.today()
                path = self._diary_path(today)
                migration_note = f"[migrated] Historical entries from HISTORY.md:\n{content}\n"
                with open(path, "a", encoding="utf-8") as f:
                    f.write(migration_note)
                migrated = True
            self._legacy_history.rename(self._legacy_history.with_suffix(".md.bak"))
            logger.info("Migrated HISTORY.md -> diary")

        return migrated
