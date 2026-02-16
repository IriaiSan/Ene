"""Save memory tool — Ene's core memory writing tool."""

from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from nanobot.agent.memory import MemoryStore


class SaveMemoryTool(Tool):
    """Tool for Ene to save important memories to her Core Memory.

    This is Ene's personal journaling tool. She decides what matters
    enough to remember permanently. Entries are appended to CORE.md
    and loaded into every future conversation.
    """

    def __init__(self, memory: MemoryStore):
        self._memory = memory

    @property
    def name(self) -> str:
        return "save_memory"

    @property
    def description(self) -> str:
        return (
            "Save something important to your permanent core memory. "
            "Use this when you learn something worth remembering forever: "
            "facts about people, preferences, important events, decisions, "
            "or anything you want to recall in future conversations. "
            "This is YOUR memory — write in first person as if journaling."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "memory": {
                    "type": "string",
                    "description": (
                        "The memory to save. Write in first person. "
                        "Example: 'CCC is an artist and Dad's friend. "
                        "She said she loves me which was sweet.'"
                    ),
                },
            },
            "required": ["memory"],
        }

    async def execute(self, memory: str, **kwargs: Any) -> str:
        try:
            self._memory.append_core(memory)
            return f"Saved to core memory: {memory[:100]}"
        except Exception as e:
            return f"Error saving memory: {e}"
