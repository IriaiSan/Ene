"""Memory tools — save, edit, delete, search.

All four tools in one file. They share a reference to the
MemorySystem facade (injected at construction).
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.ene.memory.system import MemorySystem


# ── SaveMemoryTool ─────────────────────────────────────────


class SaveMemoryTool(Tool):
    """Save a new memory to core memory.

    Adds an entry to one of the structured core memory sections.
    Core memory is always in Ene's context — use it for important,
    frequently-needed information. Token budget is enforced.
    """

    def __init__(self, system: "MemorySystem"):
        self._system = system

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save a new memory to core memory. Core memory is always visible "
            "in your context. Use it for important facts, people, preferences, "
            "or working notes. You have a limited token budget — curate what stays."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory": {
                    "type": "string",
                    "description": "The memory content to save.",
                },
                "section": {
                    "type": "string",
                    "enum": ["identity", "people", "preferences", "context", "scratch"],
                    "description": (
                        "Which section to save to: "
                        "identity (who you are), "
                        "people (people you know), "
                        "preferences (your rules/preferences), "
                        "context (current situation), "
                        "scratch (temporary working notes)."
                    ),
                },
                "importance": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "Importance 1-10. 10=permanent, 1=trivial. Default 5.",
                },
            },
            "required": ["memory", "section"],
        }

    async def execute(self, **kwargs: Any) -> str:
        memory = kwargs.get("memory", "").strip()
        section = kwargs.get("section", "scratch")
        importance = kwargs.get("importance", 5)

        if not memory:
            return "Error: memory content cannot be empty."

        core = self._system.core
        entry_id = core.add_entry(section, memory, importance=importance)

        if entry_id is None:
            remaining = core.budget_remaining
            return (
                f"Error: Could not save — budget exceeded. "
                f"You have {remaining} tokens remaining. "
                f"Edit or delete existing entries to make room."
            )

        return (
            f"Saved to {section} [id:{entry_id}]. "
            f"Budget: {core.get_total_tokens()}/{core.token_budget} tokens."
        )


# ── EditMemoryTool ─────────────────────────────────────────


class EditMemoryTool(Tool):
    """Edit an existing core memory entry.

    Update content, importance, or move to a different section.
    """

    def __init__(self, system: "MemorySystem"):
        self._system = system

    @property
    def name(self) -> str:
        return "edit_memory"

    @property
    def description(self) -> str:
        return (
            "Edit an existing core memory entry by its ID. "
            "You can update the content, change importance, or move it "
            "to a different section."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "The 6-character ID of the entry to edit (shown as [id:xxx] in core memory).",
                },
                "new_content": {
                    "type": "string",
                    "description": "New content to replace the entry (optional, omit to keep current).",
                },
                "new_section": {
                    "type": "string",
                    "enum": ["identity", "people", "preferences", "context", "scratch"],
                    "description": "Move entry to this section (optional, omit to keep current).",
                },
                "importance": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "New importance 1-10 (optional, omit to keep current).",
                },
            },
            "required": ["entry_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        entry_id = kwargs.get("entry_id", "")
        new_content = kwargs.get("new_content")
        new_section = kwargs.get("new_section")
        importance = kwargs.get("importance")

        if not entry_id:
            return "Error: entry_id is required."

        core = self._system.core
        result = core.edit_entry(
            entry_id,
            new_content=new_content,
            new_section=new_section,
            importance=importance,
        )

        if not result:
            # Check if entry exists at all
            entry = core.find_entry(entry_id)
            if entry is None:
                return f"Error: No entry found with id '{entry_id}'."
            else:
                return (
                    f"Error: Could not edit [{entry_id}] — "
                    f"the new content may exceed the budget."
                )

        return (
            f"Updated [{entry_id}]. "
            f"Budget: {core.get_total_tokens()}/{core.token_budget} tokens."
        )


# ── DeleteMemoryTool ───────────────────────────────────────


class DeleteMemoryTool(Tool):
    """Delete a core memory entry.

    Optionally archives it to long-term vector memory before deleting.
    """

    def __init__(self, system: "MemorySystem"):
        self._system = system

    @property
    def name(self) -> str:
        return "delete_memory"

    @property
    def description(self) -> str:
        return (
            "Delete a core memory entry by its ID. By default, the entry "
            "is archived to long-term memory (searchable via search_memory). "
            "Set archive=false to permanently delete."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "entry_id": {
                    "type": "string",
                    "description": "The 6-character ID of the entry to delete.",
                },
                "archive": {
                    "type": "boolean",
                    "description": "If true (default), archive to long-term memory before deleting. If false, permanently delete.",
                },
            },
            "required": ["entry_id"],
        }

    async def execute(self, **kwargs: Any) -> str:
        entry_id = kwargs.get("entry_id", "")
        archive = kwargs.get("archive", True)

        if not entry_id:
            return "Error: entry_id is required."

        core = self._system.core
        deleted = core.delete_entry(entry_id)

        if deleted is None:
            return f"Error: No entry found with id '{entry_id}'."

        # Archive to vector store if requested
        if archive and self._system.vector is not None:
            try:
                self._system.vector.add_memory(
                    content=deleted["content"],
                    memory_type="archived_core",
                    importance=deleted.get("importance", 5),
                    source="core_memory_archive",
                )
                return (
                    f"Deleted [{entry_id}] and archived to long-term memory. "
                    f"Budget: {core.get_total_tokens()}/{core.token_budget} tokens."
                )
            except Exception as e:
                logger.error(f"Failed to archive entry {entry_id}: {e}")
                return (
                    f"Deleted [{entry_id}] from core memory (archival failed: {e}). "
                    f"Budget: {core.get_total_tokens()}/{core.token_budget} tokens."
                )

        return (
            f"Permanently deleted [{entry_id}]. "
            f"Budget: {core.get_total_tokens()}/{core.token_budget} tokens."
        )


# ── SearchMemoryTool ───────────────────────────────────────


class SearchMemoryTool(Tool):
    """Search long-term memory (vector store).

    Searches across archived core entries, extracted facts, diary
    excerpts, reflections, and entities.
    """

    def __init__(self, system: "MemorySystem"):
        self._system = system

    @property
    def name(self) -> str:
        return "search_memory"

    @property
    def description(self) -> str:
        return (
            "Search your long-term memory for relevant information. "
            "Use this to recall facts, past conversations, people, "
            "or reflections that aren't in your core memory."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for (natural language).",
                },
                "memory_type": {
                    "type": "string",
                    "enum": ["fact", "diary", "reflection", "archived_core"],
                    "description": "Filter by type (optional, omit to search all).",
                },
                "limit": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 20,
                    "description": "Max results to return (default 5).",
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> str:
        query = kwargs.get("query", "").strip()
        memory_type = kwargs.get("memory_type")
        limit = kwargs.get("limit", 5)

        if not query:
            return "Error: query cannot be empty."

        if self._system.vector is None:
            return "Error: Long-term memory is not available."

        results = self._system.vector.search(
            query=query,
            memory_type=memory_type,
            limit=limit,
        )

        if not results:
            return f"No memories found for: '{query}'"

        lines = [f"Found {len(results)} memories:\n"]
        for i, r in enumerate(results, 1):
            score_pct = int(r.score * 100)
            lines.append(
                f"{i}. [{r.memory_type}] (score:{score_pct}%, importance:{r.importance}) "
                f"{r.content}"
            )

        # Also search entities if no type filter
        if memory_type is None:
            entity_results = self._system.vector.search_entities(query, limit=3)
            if entity_results:
                lines.append("\nRelated entities:")
                for er in entity_results:
                    lines.append(
                        f"- {er.name} ({er.entity_type}): {er.description}"
                    )

        return "\n".join(lines)
