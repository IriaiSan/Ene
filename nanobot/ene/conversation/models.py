"""Data models for the Conversation Tracker module.

Defines Thread and ThreadMessage dataclasses, state constants,
and configuration values for thread lifecycle management.
"""

from __future__ import annotations

import re
import uuid
import time
from dataclasses import dataclass, field
from typing import Any


# ── Thread states ────────────────────────────────────────────────────────
ACTIVE = "active"
STALE = "stale"
RESOLVED = "resolved"
DEAD = "dead"

# ── Timing constants (seconds) ──────────────────────────────────────────
THREAD_STALE_SECONDS = 300       # 5 min silence → stale
THREAD_DEAD_SECONDS = 900        # 15 min total silence → dead (archived)

# ── Capacity limits ─────────────────────────────────────────────────────
THREAD_MAX_ACTIVE_PER_CHANNEL = 10   # Max active threads per channel
THREAD_MAX_MESSAGES = 100            # Max messages stored per thread (oldest trimmed)
THREAD_TOPIC_KEYWORDS = 5           # Number of topic keywords to extract

# ── Display windowing ───────────────────────────────────────────────────
ENE_THREAD_FIRST_N = 2              # First N messages shown in long threads
ENE_THREAD_LAST_N = 4               # Last N messages shown in long threads
ENE_THREAD_SHORT_THRESHOLD = 6      # Threads with ≤ this many msgs show all
ENE_THREAD_MAX_DISPLAY = 3          # Max Ene threads shown in context
BG_THREAD_LAST_N = 3                # Messages shown per background thread
BG_THREAD_MAX_DISPLAY = 2           # Max background threads shown

# ── Scoring thresholds ──────────────────────────────────────────────────
ASSIGNMENT_THRESHOLD = 0.5           # Min score to assign msg to existing thread
MIN_MESSAGES_FOR_THREAD = 2         # Pending entries need a 2nd msg to become threads

# ── Resolution patterns ─────────────────────────────────────────────────
RESOLUTION_PATTERN = re.compile(
    r"\b(thanks|thank you|thx|ty|got it|makes sense|ok cool|understood|"
    r"perfect|nvm|nevermind|never mind|figured it out|solved|all good|"
    r"appreciate it|cheers|aight bet|np|no worries)\b",
    re.IGNORECASE,
)

# ── Topic shift markers ─────────────────────────────────────────────────
SHIFT_MARKER_PATTERN = re.compile(
    r"\b(btw|by the way|anyway|on another note|speaking of|"
    r"also unrelated|random but|unrelated but|off topic|"
    r"oh and|changing topic|side note)\b",
    re.IGNORECASE,
)


@dataclass
class ThreadMessage:
    """A single message assigned to a thread."""

    discord_msg_id: str             # Real Discord snowflake
    author_name: str                # Display name (e.g. "Azpxct")
    author_username: str            # Stable @username (e.g. "azpext_wizpxct")
    author_id: str                  # Platform ID (e.g. "discord:123456")
    content: str                    # Sanitized message text
    timestamp: float                # Unix timestamp
    reply_to_msg_id: str | None     # Discord msg ID this replies to, or None
    is_reply_to_ene: bool           # Whether this replies to one of Ene's messages
    classification: str             # "respond" or "context"
    is_ene: bool = False            # Whether this message is FROM Ene

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON storage."""
        return {
            "discord_msg_id": self.discord_msg_id,
            "author_name": self.author_name,
            "author_username": self.author_username,
            "author_id": self.author_id,
            "content": self.content,
            "timestamp": self.timestamp,
            "reply_to_msg_id": self.reply_to_msg_id,
            "is_reply_to_ene": self.is_reply_to_ene,
            "classification": self.classification,
            "is_ene": self.is_ene,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ThreadMessage:
        """Deserialize from JSON storage."""
        return cls(
            discord_msg_id=d["discord_msg_id"],
            author_name=d["author_name"],
            author_username=d.get("author_username", ""),
            author_id=d["author_id"],
            content=d["content"],
            timestamp=d["timestamp"],
            reply_to_msg_id=d.get("reply_to_msg_id"),
            is_reply_to_ene=d.get("is_reply_to_ene", False),
            classification=d.get("classification", "context"),
            is_ene=d.get("is_ene", False),
        )


@dataclass
class Thread:
    """A tracked conversation thread."""

    thread_id: str                              # UUID4
    channel_key: str                            # "discord:channel_id"
    state: str = ACTIVE                         # active/stale/resolved/dead
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    participants: set[str] = field(default_factory=set)  # Platform IDs
    ene_involved: bool = False                  # Ene is a participant

    messages: list[ThreadMessage] = field(default_factory=list)
    topic_keywords: list[str] = field(default_factory=list)

    parent_thread_id: str | None = None         # Split parent
    child_thread_ids: list[str] = field(default_factory=list)

    discord_msg_ids: set[str] = field(default_factory=set)  # For reply-chain lookup

    @staticmethod
    def new(channel_key: str) -> Thread:
        """Create a new empty thread with a fresh UUID."""
        return Thread(
            thread_id=str(uuid.uuid4()),
            channel_key=channel_key,
        )

    @property
    def message_count(self) -> int:
        return len(self.messages)

    @property
    def participant_count(self) -> int:
        return len(self.participants)

    def add_message(self, msg: ThreadMessage) -> None:
        """Add a message to this thread, updating metadata."""
        self.messages.append(msg)
        self.participants.add(msg.author_id)
        self.updated_at = msg.timestamp
        if msg.discord_msg_id:
            self.discord_msg_ids.add(msg.discord_msg_id)
        if msg.classification == "respond" or msg.is_reply_to_ene:
            self.ene_involved = True
        if msg.is_ene:
            self.ene_involved = True
        # Trim if over capacity
        if len(self.messages) > THREAD_MAX_MESSAGES:
            removed = self.messages[: len(self.messages) - THREAD_MAX_MESSAGES]
            self.messages = self.messages[-THREAD_MAX_MESSAGES:]
            for rm in removed:
                self.discord_msg_ids.discard(rm.discord_msg_id)

    def is_expired(self, now: float | None = None) -> bool:
        """Check if this thread should transition to a later state."""
        now = now or time.time()
        age = now - self.updated_at
        if self.state == ACTIVE and age > THREAD_STALE_SECONDS:
            return True
        if self.state in (STALE, RESOLVED) and age > THREAD_DEAD_SECONDS:
            return True
        return False

    def check_resolution(self, msg: ThreadMessage) -> bool:
        """Check if a message resolves this thread."""
        # Need at least 2 participants and 3 messages for resolution
        if self.participant_count < 2 or self.message_count < 3:
            return False
        return bool(RESOLUTION_PATTERN.search(msg.content))

    def to_dict(self) -> dict[str, Any]:
        """Serialize for JSON storage."""
        return {
            "thread_id": self.thread_id,
            "channel_key": self.channel_key,
            "state": self.state,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "participants": list(self.participants),
            "ene_involved": self.ene_involved,
            "messages": [m.to_dict() for m in self.messages],
            "topic_keywords": self.topic_keywords,
            "parent_thread_id": self.parent_thread_id,
            "child_thread_ids": self.child_thread_ids,
            "discord_msg_ids": list(self.discord_msg_ids),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Thread:
        """Deserialize from JSON storage."""
        t = cls(
            thread_id=d["thread_id"],
            channel_key=d["channel_key"],
            state=d.get("state", ACTIVE),
            created_at=d.get("created_at", 0.0),
            updated_at=d.get("updated_at", 0.0),
            participants=set(d.get("participants", [])),
            ene_involved=d.get("ene_involved", False),
            messages=[ThreadMessage.from_dict(m) for m in d.get("messages", [])],
            topic_keywords=d.get("topic_keywords", []),
            parent_thread_id=d.get("parent_thread_id"),
            child_thread_ids=d.get("child_thread_ids", []),
            discord_msg_ids=set(d.get("discord_msg_ids", [])),
        )
        return t


@dataclass
class PendingMessage:
    """A message waiting for a second related message to form a thread.

    Single messages that don't match any existing thread go here.
    They only become a full Thread when a second message arrives that
    scores above threshold against them.
    """

    message: ThreadMessage
    channel_key: str
    created_at: float = field(default_factory=time.time)
    discord_msg_id: str = ""

    def __post_init__(self) -> None:
        if not self.discord_msg_id:
            self.discord_msg_id = self.message.discord_msg_id
