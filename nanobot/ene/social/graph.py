"""Social graph — connection queries between people.

Provides methods to query relationships, find mutual connections,
trace connection chains (BFS), and render connections for context injection.
"""

from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.ene.social.person import PersonRegistry, Connection


class SocialGraph:
    """Query social connections from person profiles.

    Operates on the PersonRegistry's data — no separate storage.
    All queries traverse the person profiles' connection lists.
    """

    def __init__(self, registry: "PersonRegistry") -> None:
        self._registry = registry

    def get_connections(self, person_id: str) -> list["Connection"]:
        """Get all direct connections for a person."""
        profile = self._registry.get_by_id(person_id)
        if profile is None:
            return []
        return list(profile.connections)

    def get_mutual_connections(
        self, person_a: str, person_b: str
    ) -> list[str]:
        """Get person IDs that both people are connected to.

        Returns list of person_ids connected to both A and B.
        """
        a_conns = {c.person_id for c in self.get_connections(person_a)}
        b_conns = {c.person_id for c in self.get_connections(person_b)}
        return sorted(a_conns & b_conns)

    def get_connection_chain(
        self,
        person_a: str,
        person_b: str,
        max_depth: int = 3,
    ) -> list[str] | None:
        """Find shortest path between two people via connections (BFS).

        Returns list of person_ids forming the path (inclusive of both ends),
        or None if no path exists within max_depth.
        """
        if person_a == person_b:
            return [person_a]

        visited = {person_a}
        queue: deque[list[str]] = deque([[person_a]])

        while queue:
            path = queue.popleft()
            edges_used = len(path) - 1

            # Already at max depth — can't go further
            if edges_used >= max_depth:
                continue

            current = path[-1]
            for conn in self.get_connections(current):
                if conn.person_id == person_b:
                    return path + [conn.person_id]
                if conn.person_id not in visited:
                    visited.add(conn.person_id)
                    queue.append(path + [conn.person_id])

        return None

    def render_for_context(self, person_id: str) -> str:
        """Render a person's connections as a context string.

        Example: "Connected to: Dad (friend), CCC (acquaintance)"
        """
        connections = self.get_connections(person_id)
        if not connections:
            return ""

        parts = []
        for conn in connections:
            other = self._registry.get_by_id(conn.person_id)
            name = other.display_name if other else conn.person_id
            parts.append(f"{name} ({conn.relationship})")

        return "Connected to: " + ", ".join(parts)
