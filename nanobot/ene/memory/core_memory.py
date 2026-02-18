"""CoreMemory — Ene's permanent, editable, token-budgeted memory.

Stored as core.json with named sections. Each entry has a short ID
so Ene can reference it for editing or deletion. Token budget is
enforced per-section and globally — Ene must curate what stays.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import tiktoken
from loguru import logger

# Use cl100k_base — good approximation for most models including DeepSeek
_ENCODER = tiktoken.get_encoding("cl100k_base")

# Default section layout with per-section token budgets
DEFAULT_SECTIONS: dict[str, dict[str, Any]] = {
    "identity": {"label": "Who I Am", "max_tokens": 600, "entries": []},
    "people": {"label": "People I Know", "max_tokens": 1200, "entries": []},
    "preferences": {"label": "Preferences & Rules", "max_tokens": 800, "entries": []},
    "context": {"label": "Current Context", "max_tokens": 600, "entries": []},
    "scratch": {"label": "Working Notes", "max_tokens": 800, "entries": []},
}

SECTION_NAMES = list(DEFAULT_SECTIONS.keys())


def _short_id() -> str:
    """Generate a short unique ID (6 hex chars)."""
    return uuid.uuid4().hex[:6]


def _count_tokens(text: str) -> int:
    """Count tokens in a string using tiktoken."""
    return len(_ENCODER.encode(text))


class CoreMemory:
    """Manages Ene's structured, token-budgeted core memory.

    Core memory is always loaded into the system prompt. It has
    a hard token budget that Ene must manage — when full, she must
    edit or delete entries to make room.
    """

    def __init__(self, memory_dir: Path, token_budget: int = 4000):
        self.path = memory_dir / "core.json"
        self.token_budget = token_budget
        self._data: dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Load core.json from disk, or initialize empty if missing."""
        if self.path.exists():
            try:
                raw = self.path.read_text(encoding="utf-8")
                self._data = json.loads(raw)
                # Ensure all default sections exist (forward compat)
                for name, defaults in DEFAULT_SECTIONS.items():
                    if name not in self._data.get("sections", {}):
                        self._data.setdefault("sections", {})[name] = {
                            "label": defaults["label"],
                            "max_tokens": defaults["max_tokens"],
                            "entries": [],
                        }
                self._recount()
                return
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Corrupt core.json, reinitializing: {e}")

        # Initialize empty
        self._data = {
            "version": 2,
            "sections": {
                name: {
                    "label": sec["label"],
                    "max_tokens": sec["max_tokens"],
                    "entries": [],
                }
                for name, sec in DEFAULT_SECTIONS.items()
            },
            "token_budget": self.token_budget,
            "token_count": 0,
        }

    def save(self) -> None:
        """Persist core.json to disk."""
        self._recount()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def _recount(self) -> None:
        """Recalculate total and per-section token counts."""
        total = 0
        for sec in self._data.get("sections", {}).values():
            sec_tokens = sum(_count_tokens(e["content"]) for e in sec.get("entries", []))
            total += sec_tokens
        self._data["token_count"] = total

    # ── CRUD Operations ────────────────────────────────────

    def add_entry(
        self,
        section: str,
        content: str,
        importance: int = 5,
    ) -> str | None:
        """Add a new entry to a section.

        Returns:
            Entry ID on success, None if over budget.
        Sets self._last_add_error to a descriptive reason on failure.
        """
        self._last_add_error: str | None = None

        if section not in self._data.get("sections", {}):
            self._last_add_error = f"Unknown section '{section}'."
            return None

        sec = self._data["sections"][section]
        new_tokens = _count_tokens(content)

        # Check section budget
        sec_tokens = sum(_count_tokens(e["content"]) for e in sec["entries"])
        if sec_tokens + new_tokens > sec["max_tokens"]:
            self._last_add_error = (
                f"Section '{section}' is full "
                f"({sec_tokens}/{sec['max_tokens']} tokens, need {new_tokens} more). "
                f"Delete or edit entries in this section to make room."
            )
            return None

        # Check global budget
        if self._data.get("token_count", 0) + new_tokens > self.token_budget:
            self._last_add_error = (
                f"Global budget full "
                f"({self._data.get('token_count', 0)}/{self.token_budget} tokens). "
                f"Delete entries from any section to make room."
            )
            return None

        entry_id = _short_id()
        now = datetime.now().isoformat(timespec="seconds")
        entry = {
            "id": entry_id,
            "content": content.strip(),
            "importance": max(1, min(10, importance)),
            "created_at": now,
            "updated_at": now,
        }
        sec["entries"].append(entry)
        self._recount()
        self.save()
        logger.info(f"Core memory added [{entry_id}] to {section}: {content[:60]}")
        return entry_id

    def edit_entry(
        self,
        entry_id: str,
        new_content: str | None = None,
        new_section: str | None = None,
        importance: int | None = None,
    ) -> bool:
        """Edit an existing entry.

        Returns True if found and updated, False if not found.
        """
        entry, current_section = self._find_entry_and_section(entry_id)
        if entry is None or current_section is None:
            return False

        # If moving to a new section, check its budget
        if new_section and new_section != current_section:
            if new_section not in self._data.get("sections", {}):
                return False
            target_sec = self._data["sections"][new_section]
            content_to_check = new_content if new_content is not None else entry["content"]
            target_tokens = sum(_count_tokens(e["content"]) for e in target_sec["entries"])
            if target_tokens + _count_tokens(content_to_check) > target_sec["max_tokens"]:
                return False

        # If changing content, check budgets
        if new_content is not None:
            old_tokens = _count_tokens(entry["content"])
            new_tokens = _count_tokens(new_content)
            token_delta = new_tokens - old_tokens
            if self._data.get("token_count", 0) + token_delta > self.token_budget:
                return False
            entry["content"] = new_content.strip()

        if importance is not None:
            entry["importance"] = max(1, min(10, importance))

        entry["updated_at"] = datetime.now().isoformat(timespec="seconds")

        # Move to new section if requested
        if new_section and new_section != current_section:
            self._data["sections"][current_section]["entries"].remove(entry)
            self._data["sections"][new_section]["entries"].append(entry)

        self._recount()
        self.save()
        logger.info(f"Core memory edited [{entry_id}]")
        return True

    def delete_entry(self, entry_id: str) -> dict | None:
        """Delete an entry from core memory.

        Returns the deleted entry dict (for archival), or None if not found.
        """
        entry, section_name = self._find_entry_and_section(entry_id)
        if entry is None or section_name is None:
            return None

        self._data["sections"][section_name]["entries"].remove(entry)
        self._recount()
        self.save()
        logger.info(f"Core memory deleted [{entry_id}] from {section_name}")
        return entry

    def find_entry(self, entry_id: str) -> dict | None:
        """Find an entry by ID across all sections."""
        entry, _ = self._find_entry_and_section(entry_id)
        return entry

    def _find_entry_and_section(self, entry_id: str) -> tuple[dict | None, str | None]:
        """Find an entry and its section name by ID."""
        for sec_name, sec in self._data.get("sections", {}).items():
            for entry in sec.get("entries", []):
                if entry.get("id") == entry_id:
                    return entry, sec_name
        return None, None

    # ── Query / Rendering ──────────────────────────────────

    def get_total_tokens(self) -> int:
        """Current total token count across all sections."""
        self._recount()
        return self._data.get("token_count", 0)

    def get_section_tokens(self, section: str) -> int:
        """Token count for a specific section."""
        sec = self._data.get("sections", {}).get(section)
        if not sec:
            return 0
        return sum(_count_tokens(e["content"]) for e in sec.get("entries", []))

    def get_all_entries(self) -> list[tuple[str, dict]]:
        """Return all entries as (section_name, entry) tuples."""
        result = []
        for sec_name, sec in self._data.get("sections", {}).items():
            for entry in sec.get("entries", []):
                result.append((sec_name, entry))
        return result

    def render_for_context(self) -> str:
        """Render core memory as markdown for system prompt injection.

        Format:
            ## Core Memory (2847/4000 tokens)
            ### Who I Am
            - I'm Ene. Dad built me. [id:a1b2c3]
            ### People I Know
            - CCC is an artist. [id:ppl_002]
        """
        self._recount()
        total = self._data.get("token_count", 0)
        lines = [f"## Core Memory ({total}/{self.token_budget} tokens)\n"]

        for sec_name, sec in self._data.get("sections", {}).items():
            entries = sec.get("entries", [])
            if not entries:
                continue
            # Label context section to signal it's background, not current conversation
            if sec_name == "context":
                lines.append(f"### {sec['label']} (background notes, NOT current conversation)")
            else:
                lines.append(f"### {sec['label']}")
            for entry in entries:
                lines.append(f"- {entry['content']} [id:{entry['id']}]")
            lines.append("")  # blank line between sections

        return "\n".join(lines).strip()

    @property
    def is_over_budget(self) -> bool:
        """Whether total tokens exceed the budget."""
        return self.get_total_tokens() > self.token_budget

    @property
    def budget_remaining(self) -> int:
        """Tokens remaining in the budget."""
        return max(0, self.token_budget - self.get_total_tokens())
