"""Session management for conversation history."""

import json
from pathlib import Path
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from loguru import logger

from nanobot.utils.helpers import ensure_dir, safe_filename


@dataclass
class Session:
    """
    A conversation session.

    Stores messages in JSONL format for easy reading and persistence.

    Important: Messages are append-only for LLM cache efficiency.
    The consolidation process writes summaries to MEMORY.md/HISTORY.md
    but does NOT modify the messages list or get_history() output.
    """

    key: str  # channel:chat_id
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)
    last_consolidated: int = 0  # Number of messages already consolidated to files
    
    def add_message(self, role: str, content: str, **kwargs: Any) -> None:
        """Add a message to the session."""
        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            **kwargs
        }
        self.messages.append(msg)
        self.updated_at = datetime.now()
    
    def get_history(self, max_messages: int = 500) -> list[dict[str, Any]]:
        """Get recent messages in LLM format (role + content only)."""
        return [{"role": m["role"], "content": m["content"]} for m in self.messages[-max_messages:]]

    def get_responded_count(self) -> int:
        """Count messages where Ene actually responded (assistant role)."""
        return sum(1 for m in self.messages if m.get("role") == "assistant")

    def get_hybrid_history(
        self,
        recent_count: int = 20,
        summary: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get history with optional summary of older messages + verbatim recent.

        Structure (per "Lost in the Middle" research — Liu et al. 2024):
        LLMs attend best to START and END of context. So:
        - Summary of older context goes FIRST in history (top of middle zone)
        - Recent verbatim messages go LAST (near current message = high attention)

        Args:
            recent_count: Number of recent messages to include verbatim.
            summary: Optional summary of older messages to prepend.

        Returns:
            List of messages in LLM format.
        """
        messages = []

        # Older context summary (placed first — top of middle zone).
        # No synthetic assistant acknowledgement: a fake "I remember..." utterance
        # in history can cause the model to skip actually reading the summary.
        if summary:
            messages.append({
                "role": "user",
                "content": f"[Earlier conversation summary]\n{summary}"
            })

        # Recent verbatim messages (placed last — high attention zone)
        recent = self.messages[-recent_count:] if len(self.messages) > recent_count else self.messages
        for m in recent:
            messages.append({"role": m["role"], "content": m["content"]})

        return messages

    def estimate_tokens(self) -> int:
        """Estimate total token count of all messages.

        Uses the rough heuristic: 1 token ≈ 4 characters (English average).
        This avoids importing tiktoken for every message check while being
        accurate enough for budget decisions.
        """
        total_chars = sum(len(m.get("content", "")) for m in self.messages)
        return total_chars // 4

    def clear(self) -> None:
        """Clear all messages and reset session to initial state."""
        self.messages = []
        self.last_consolidated = 0
        self.updated_at = datetime.now()


class SessionManager:
    """
    Manages conversation sessions.

    Sessions are stored as JSONL files in the sessions directory.
    """

    def __init__(self, workspace: Path, sessions_dir: Path | None = None):
        self.workspace = workspace
        # Lab harness passes custom sessions_dir for state isolation.
        # Default: ~/.nanobot/sessions (backwards compatible).
        self.sessions_dir = ensure_dir(
            sessions_dir if sessions_dir is not None
            else (Path.home() / ".nanobot" / "sessions")
        )
        self._cache: dict[str, Session] = {}
    
    def _get_session_path(self, key: str) -> Path:
        """Get the file path for a session."""
        safe_key = safe_filename(key.replace(":", "_"))
        return self.sessions_dir / f"{safe_key}.jsonl"
    
    def get_or_create(self, key: str) -> Session:
        """
        Get an existing session or create a new one.
        
        Args:
            key: Session key (usually channel:chat_id).
        
        Returns:
            The session.
        """
        if key in self._cache:
            return self._cache[key]
        
        session = self._load(key)
        if session is None:
            session = Session(key=key)
        
        self._cache[key] = session
        return session
    
    def _load(self, key: str) -> Session | None:
        """Load a session from disk."""
        path = self._get_session_path(key)

        if not path.exists():
            return None

        try:
            messages = []
            metadata = {}
            created_at = None
            last_consolidated = 0

            with open(path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue

                    data = json.loads(line)

                    if data.get("_type") == "metadata":
                        metadata = data.get("metadata", {})
                        created_at = datetime.fromisoformat(data["created_at"]) if data.get("created_at") else None
                        last_consolidated = data.get("last_consolidated", 0)
                    else:
                        messages.append(data)

            return Session(
                key=key,
                messages=messages,
                created_at=created_at or datetime.now(),
                metadata=metadata,
                last_consolidated=last_consolidated
            )
        except Exception as e:
            logger.warning(f"Failed to load session {key}: {e}")
            return None
    
    def save(self, session: Session) -> None:
        """Save a session to disk."""
        path = self._get_session_path(session.key)

        with open(path, "w", encoding="utf-8") as f:
            metadata_line = {
                "_type": "metadata",
                "created_at": session.created_at.isoformat(),
                "updated_at": session.updated_at.isoformat(),
                "metadata": session.metadata,
                "last_consolidated": session.last_consolidated
            }
            f.write(json.dumps(metadata_line) + "\n")
            for msg in session.messages:
                f.write(json.dumps(msg) + "\n")

        self._cache[session.key] = session
    
    def invalidate(self, key: str) -> None:
        """Remove a session from the in-memory cache."""
        self._cache.pop(key, None)
    
    def list_sessions(self) -> list[dict[str, Any]]:
        """
        List all sessions.
        
        Returns:
            List of session info dicts.
        """
        sessions = []
        
        for path in self.sessions_dir.glob("*.jsonl"):
            try:
                # Read just the metadata line
                with open(path, encoding="utf-8") as f:
                    first_line = f.readline().strip()
                    if first_line:
                        data = json.loads(first_line)
                        if data.get("_type") == "metadata":
                            sessions.append({
                                "key": path.stem.replace("_", ":"),
                                "created_at": data.get("created_at"),
                                "updated_at": data.get("updated_at"),
                                "path": str(path)
                            })
            except Exception:
                continue
        
        return sorted(sessions, key=lambda x: x.get("updated_at", ""), reverse=True)
