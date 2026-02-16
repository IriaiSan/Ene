"""Social tools — update_person_note, view_person, list_people.

These tools let Ene manage her knowledge about people.
All tools share a reference to the PersonRegistry.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.ene.social.person import PersonRegistry
    from nanobot.ene.social.graph import SocialGraph


# ── UpdatePersonNoteTool ──────────────────────────────────


class UpdatePersonNoteTool(Tool):
    """Add a note to a person's profile.

    Ene uses this to record things she learns about people —
    interests, facts, impressions, relationship details.
    """

    def __init__(self, registry: "PersonRegistry") -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "update_person_note"

    @property
    def description(self) -> str:
        return (
            "Add a note about a person to their social profile. "
            "Use this to record things you learn about people — "
            "interests, important facts, relationship details. "
            "Notes persist across sessions."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "person_name": {
                    "type": "string",
                    "description": (
                        "The person's display name or alias. "
                        "Case-insensitive search."
                    ),
                },
                "note": {
                    "type": "string",
                    "description": "What to record about this person.",
                },
            },
            "required": ["person_name", "note"],
        }

    async def execute(self, **kwargs: Any) -> str:
        person_name = kwargs.get("person_name", "").strip()
        note = kwargs.get("note", "").strip()

        if not person_name:
            return "Error: person_name is required."
        if not note:
            return "Error: note content is required."

        profile = self._registry.find_by_name(person_name)
        if profile is None:
            return f"I don't know anyone named '{person_name}'."

        result = self._registry.add_note(profile.id, note)
        if result:
            return (
                f"Noted about {profile.display_name}: {note}\n"
                f"({len(profile.notes) + 1} notes total)"
            )
        return f"Error: Could not add note for {person_name}."


# ── ViewPersonTool ────────────────────────────────────────


class ViewPersonTool(Tool):
    """View detailed info about a person.

    Ene uses this to look up someone's profile, notes,
    trust level, and connections.
    """

    def __init__(
        self, registry: "PersonRegistry", graph: "SocialGraph"
    ) -> None:
        self._registry = registry
        self._graph = graph

    @property
    def name(self) -> str:
        return "view_person"

    @property
    def description(self) -> str:
        return (
            "View detailed information about a person — "
            "their profile, notes, trust level, and social connections. "
            "Use this when you want to look someone up."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "person_name": {
                    "type": "string",
                    "description": "The person's display name or alias.",
                },
            },
            "required": ["person_name"],
        }

    async def execute(self, **kwargs: Any) -> str:
        person_name = kwargs.get("person_name", "").strip()
        if not person_name:
            return "Error: person_name is required."

        profile = self._registry.find_by_name(person_name)
        if profile is None:
            return f"I don't know anyone named '{person_name}'."

        # Build the detail view
        lines = [
            f"## {profile.display_name}",
            f"Trust: {profile.trust.tier} ({int(profile.trust.score * 100)}%)",
            f"Known since: {profile.created_at[:10] if profile.created_at else 'unknown'}",
            f"Messages: {profile.trust.signals.get('message_count', 0)} "
            f"across {profile.trust.signals.get('session_count', 0)} sessions",
        ]

        if profile.summary:
            lines.append(f"\n**Summary:** {profile.summary}")

        # Connections
        conn_str = self._graph.render_for_context(profile.id)
        if conn_str:
            lines.append(f"\n{conn_str}")

        # Aliases
        if len(profile.aliases) > 1:
            lines.append(f"Also known as: {', '.join(profile.aliases)}")

        # Platforms
        platforms = []
        for pid, ident in profile.platform_ids.items():
            platforms.append(f"{pid} ({ident.display_name})")
        if platforms:
            lines.append(f"Platforms: {', '.join(platforms)}")

        # Notes (most recent 5)
        if profile.notes:
            lines.append(f"\n**Notes** ({len(profile.notes)} total):")
            for note in profile.notes[-5:]:
                lines.append(f"- {note.content}")

        return "\n".join(lines)


# ── ListPeopleTool ────────────────────────────────────────


class ListPeopleTool(Tool):
    """List all known people with their trust tiers.

    Provides a summary of everyone Ene knows.
    """

    def __init__(self, registry: "PersonRegistry") -> None:
        self._registry = registry

    @property
    def name(self) -> str:
        return "list_people"

    @property
    def description(self) -> str:
        return (
            "List all people you know with their trust tiers. "
            "Use this to see everyone in your social circle."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {},
        }

    async def execute(self, **kwargs: Any) -> str:
        all_profiles = self._registry.get_all()
        if not all_profiles:
            return "I don't know anyone yet."

        # Sort by trust score (descending)
        all_profiles.sort(key=lambda p: p.trust.score, reverse=True)

        lines = [f"**People I know ({len(all_profiles)}):**\n"]
        for profile in all_profiles:
            trust_pct = int(profile.trust.score * 100)
            msg_count = profile.trust.signals.get("message_count", 0)
            summary_short = (
                profile.summary[:60] + "..."
                if len(profile.summary) > 60
                else profile.summary
            )
            lines.append(
                f"- **{profile.display_name}** "
                f"({profile.trust.tier}, {trust_pct}%) "
                f"— {msg_count} msgs"
                + (f" — {summary_short}" if summary_short else "")
            )

        return "\n".join(lines)
