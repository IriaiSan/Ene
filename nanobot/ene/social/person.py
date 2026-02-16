"""Person profiles and registry — identity, notes, trust data, connections.

Each person is stored as a separate JSON file in memory/social/people/.
An index.json provides O(1) platform-ID-to-person lookup.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger


# ── Dad's platform IDs (hardcoded, immutable) ─────────────

DAD_IDS = frozenset({
    "discord:1175414972482846813",
    "telegram:8559611823",
})

# ── Dataclasses ───────────────────────────────────────────


@dataclass
class PlatformIdentity:
    """A person's identity on a specific platform."""
    username: str
    display_name: str
    first_seen: str
    last_seen: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> PlatformIdentity:
        return cls(**d)


@dataclass
class Note:
    """A timestamped note about a person."""
    content: str
    added_at: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Note:
        return cls(**d)


@dataclass
class Connection:
    """A link between two people."""
    person_id: str
    relationship: str  # "friend", "acquaintance", "family", etc.
    context: str       # How they know each other

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Connection:
        return cls(**d)


@dataclass
class Violation:
    """A trust violation event."""
    description: str
    severity: float    # 0.05 to 0.50
    timestamp: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> Violation:
        return cls(**d)


@dataclass
class TrustData:
    """Trust scoring data for a person."""
    score: float = 0.0
    tier: str = "stranger"
    positive_interactions: int = 0
    negative_interactions: int = 0
    signals: dict = field(default_factory=lambda: {
        "message_count": 0,
        "session_count": 0,
        "first_interaction": "",
        "last_interaction": "",
        "days_active": 0,
        "unique_hours": [],
        "unique_days_of_week": [],
        "restricted_tool_attempts": 0,
    })
    sentiment_modifier: float = 0.0
    violations: list = field(default_factory=list)   # list[Violation dicts]
    manual_override: float | None = None
    history: list = field(default_factory=list)       # daily snapshots

    def to_dict(self) -> dict:
        return {
            "score": self.score,
            "tier": self.tier,
            "positive_interactions": self.positive_interactions,
            "negative_interactions": self.negative_interactions,
            "signals": dict(self.signals),
            "sentiment_modifier": self.sentiment_modifier,
            "violations": [v if isinstance(v, dict) else v.to_dict()
                           for v in self.violations],
            "manual_override": self.manual_override,
            "history": list(self.history),
        }

    @classmethod
    def from_dict(cls, d: dict) -> TrustData:
        return cls(
            score=d.get("score", 0.0),
            tier=d.get("tier", "stranger"),
            positive_interactions=d.get("positive_interactions", 0),
            negative_interactions=d.get("negative_interactions", 0),
            signals=d.get("signals", {}),
            sentiment_modifier=d.get("sentiment_modifier", 0.0),
            violations=d.get("violations", []),
            manual_override=d.get("manual_override"),
            history=d.get("history", []),
        )


@dataclass
class PersonProfile:
    """Complete profile for a person Ene knows."""
    id: str
    display_name: str
    aliases: list[str] = field(default_factory=list)
    platform_ids: dict[str, PlatformIdentity] = field(default_factory=dict)
    summary: str = ""
    notes: list[Note] = field(default_factory=list)
    trust: TrustData = field(default_factory=TrustData)
    connections: list[Connection] = field(default_factory=list)
    entity_id: str | None = None
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "aliases": list(self.aliases),
            "platform_ids": {
                k: v.to_dict() if isinstance(v, PlatformIdentity) else v
                for k, v in self.platform_ids.items()
            },
            "summary": self.summary,
            "notes": [n.to_dict() if isinstance(n, Note) else n for n in self.notes],
            "trust": self.trust.to_dict() if isinstance(self.trust, TrustData) else self.trust,
            "connections": [c.to_dict() if isinstance(c, Connection) else c for c in self.connections],
            "entity_id": self.entity_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PersonProfile:
        return cls(
            id=d["id"],
            display_name=d["display_name"],
            aliases=d.get("aliases", []),
            platform_ids={
                k: PlatformIdentity.from_dict(v) if isinstance(v, dict) else v
                for k, v in d.get("platform_ids", {}).items()
            },
            summary=d.get("summary", ""),
            notes=[Note.from_dict(n) if isinstance(n, dict) else n
                   for n in d.get("notes", [])],
            trust=TrustData.from_dict(d["trust"]) if isinstance(d.get("trust"), dict) else TrustData(),
            connections=[Connection.from_dict(c) if isinstance(c, dict) else c
                         for c in d.get("connections", [])],
            entity_id=d.get("entity_id"),
            created_at=d.get("created_at", ""),
            updated_at=d.get("updated_at", ""),
        )


# ── PersonRegistry ────────────────────────────────────────


class PersonRegistry:
    """Manages person profiles on disk. One JSON file per person.

    Directory structure:
        social/
        ├── index.json          # platform_id → person_id mapping
        └── people/
            ├── p_abc123.json   # Individual person files
            └── p_dad001.json
    """

    def __init__(self, social_dir: Path) -> None:
        self._social_dir = social_dir
        self._people_dir = social_dir / "people"
        self._index_path = social_dir / "index.json"
        self._index: dict[str, str] = {}  # platform_id → person_id
        self._cache: dict[str, PersonProfile] = {}  # person_id → profile

        # Ensure directories exist
        self._social_dir.mkdir(parents=True, exist_ok=True)
        self._people_dir.mkdir(exist_ok=True)

        # Load index
        self.load_index()

        # Ensure Dad exists
        self._ensure_dad()

    def load_index(self) -> None:
        """Load the platform-to-person index from disk."""
        if self._index_path.exists():
            try:
                data = json.loads(self._index_path.read_text(encoding="utf-8"))
                self._index = data.get("platform_to_person", {})
            except (json.JSONDecodeError, KeyError) as e:
                logger.error(f"Failed to load social index: {e}")
                self._index = {}
        else:
            self._index = {}

    def save_index(self) -> None:
        """Save the platform-to-person index to disk."""
        data = {
            "version": 1,
            "platform_to_person": self._index,
            "person_count": len(set(self._index.values())),
            "updated_at": _now_iso(),
        }
        self._index_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    def get_by_platform_id(self, platform_id: str) -> PersonProfile | None:
        """Look up a person by their platform ID (e.g., 'discord:123456')."""
        person_id = self._index.get(platform_id)
        if person_id is None:
            return None
        return self.get_by_id(person_id)

    def get_by_id(self, person_id: str) -> PersonProfile | None:
        """Load a person by their internal ID."""
        # Check cache
        if person_id in self._cache:
            return self._cache[person_id]

        # Load from disk
        path = self._people_dir / f"{person_id}.json"
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            profile = PersonProfile.from_dict(data)
            self._cache[person_id] = profile
            return profile
        except (json.JSONDecodeError, KeyError) as e:
            logger.error(f"Failed to load person {person_id}: {e}")
            return None

    def create(
        self,
        platform_id: str,
        display_name: str,
        metadata: dict | None = None,
    ) -> PersonProfile:
        """Create a new person profile.

        Args:
            platform_id: Platform-specific ID (e.g., "discord:123456").
            display_name: How they appear (e.g., "CCC").
            metadata: Channel-specific data (username, etc.).

        Returns:
            The newly created PersonProfile.
        """
        metadata = metadata or {}
        now = _now_iso()
        person_id = self._generate_id()

        platform_identity = PlatformIdentity(
            username=metadata.get("username", ""),
            display_name=display_name,
            first_seen=now,
            last_seen=now,
        )

        profile = PersonProfile(
            id=person_id,
            display_name=display_name,
            aliases=[display_name] if display_name else [],
            platform_ids={platform_id: platform_identity},
            trust=TrustData(
                signals={
                    "message_count": 0,
                    "session_count": 0,
                    "first_interaction": now,
                    "last_interaction": now,
                    "days_active": 0,
                    "unique_hours": [],
                    "unique_days_of_week": [],
                    "restricted_tool_attempts": 0,
                },
            ),
            created_at=now,
            updated_at=now,
        )

        # Save profile and update index
        self._save_profile(profile)
        self._index[platform_id] = person_id
        self.save_index()

        logger.info(f"Created person: {display_name} ({person_id}) via {platform_id}")
        return profile

    def update(self, profile: PersonProfile) -> None:
        """Save an updated profile to disk."""
        profile.updated_at = _now_iso()
        self._save_profile(profile)
        self._cache[profile.id] = profile

    def record_interaction(
        self,
        platform_id: str,
        display_name: str,
        metadata: dict | None = None,
        is_positive: bool = True,
    ) -> PersonProfile:
        """Record an interaction and update signals. Creates profile if new.

        This is the main entry point called on every message.
        Returns the (possibly new) person profile.
        """
        metadata = metadata or {}
        now = _now_iso()
        now_dt = datetime.now(timezone.utc)
        hour = now_dt.hour
        dow = now_dt.weekday()  # 0=Monday, 6=Sunday

        # Look up or create
        profile = self.get_by_platform_id(platform_id)
        if profile is None:
            profile = self.create(platform_id, display_name, metadata)

        # Update platform identity
        if platform_id in profile.platform_ids:
            pid = profile.platform_ids[platform_id]
            pid.last_seen = now
            if display_name and display_name != pid.display_name:
                pid.display_name = display_name
                if display_name not in profile.aliases:
                    profile.aliases.append(display_name)
        else:
            # New platform for existing person
            profile.platform_ids[platform_id] = PlatformIdentity(
                username=metadata.get("username", ""),
                display_name=display_name,
                first_seen=now,
                last_seen=now,
            )
            self._index[platform_id] = profile.id
            self.save_index()

        # Update trust signals
        signals = profile.trust.signals
        signals["message_count"] = signals.get("message_count", 0) + 1
        signals["last_interaction"] = now

        # Track unique hours for timing entropy
        unique_hours = signals.get("unique_hours", [])
        if hour not in unique_hours:
            unique_hours.append(hour)
            signals["unique_hours"] = unique_hours

        # Track unique days of week
        unique_dow = signals.get("unique_days_of_week", [])
        if dow not in unique_dow:
            unique_dow.append(dow)
            signals["unique_days_of_week"] = unique_dow

        # Track days active (approximate: count unique dates)
        first_str = signals.get("first_interaction", now)
        try:
            first_dt = datetime.fromisoformat(first_str.replace("Z", "+00:00"))
            if first_dt.tzinfo is None:
                first_dt = first_dt.replace(tzinfo=timezone.utc)
            days_known = max(0, (now_dt - first_dt).days)
        except (ValueError, AttributeError):
            days_known = 0

        # Simple session detection: new session if >30 min since last interaction
        last_str = signals.get("last_interaction", now)
        try:
            last_dt = datetime.fromisoformat(last_str.replace("Z", "+00:00"))
            if last_dt.tzinfo is None:
                last_dt = last_dt.replace(tzinfo=timezone.utc)
            gap_seconds = (now_dt - last_dt).total_seconds()
            if gap_seconds > 1800:  # 30 minutes
                signals["session_count"] = signals.get("session_count", 0) + 1
        except (ValueError, AttributeError):
            pass

        # Recalculate days_active as unique active dates
        # Approximate: use days_known but capped by message density
        # (More precise tracking would need a date list, but that grows unbounded)
        # Simple heuristic: days_active = min(days_known + 1, message_count / 2)
        msg_count = signals.get("message_count", 1)
        signals["days_active"] = min(days_known + 1, max(1, msg_count // 2))

        # Update positive/negative count
        if is_positive:
            profile.trust.positive_interactions += 1
        else:
            profile.trust.negative_interactions += 1

        # Save
        self.update(profile)
        return profile

    def get_all(self) -> list[PersonProfile]:
        """Load all person profiles."""
        profiles = []
        for path in self._people_dir.glob("p_*.json"):
            person_id = path.stem
            profile = self.get_by_id(person_id)
            if profile:
                profiles.append(profile)
        return profiles

    def add_connection(
        self,
        person_id: str,
        other_id: str,
        relationship: str,
        context: str,
    ) -> bool:
        """Add a bidirectional connection between two people.

        Returns True if successful.
        """
        person = self.get_by_id(person_id)
        other = self.get_by_id(other_id)

        if person is None or other is None:
            return False

        # Add connection to person (if not already there)
        if not any(c.person_id == other_id for c in person.connections):
            person.connections.append(Connection(
                person_id=other_id,
                relationship=relationship,
                context=context,
            ))
            self.update(person)

        # Add reverse connection to other (if not already there)
        if not any(c.person_id == person_id for c in other.connections):
            other.connections.append(Connection(
                person_id=person_id,
                relationship=relationship,
                context=context,
            ))
            self.update(other)

        return True

    def record_violation(
        self,
        platform_id: str,
        description: str,
        severity: float = 0.15,
    ) -> PersonProfile | None:
        """Record a trust violation for a person.

        Returns updated profile or None if person not found.
        """
        profile = self.get_by_platform_id(platform_id)
        if profile is None:
            return None

        violation = {
            "description": description,
            "severity": max(0.05, min(0.50, severity)),  # clamp
            "timestamp": _now_iso(),
        }
        profile.trust.violations.append(violation)
        self.update(profile)

        logger.warning(
            f"Trust violation for {profile.display_name}: "
            f"{description} (severity={severity})"
        )
        return profile

    def add_note(self, person_id: str, content: str) -> bool:
        """Add a note to a person's profile.

        Returns True if successful.
        """
        profile = self.get_by_id(person_id)
        if profile is None:
            return False

        profile.notes.append(Note(content=content, added_at=_now_iso()))

        # Keep notes manageable — cap at 50
        if len(profile.notes) > 50:
            profile.notes = profile.notes[-50:]

        self.update(profile)
        return True

    def find_by_name(self, name: str) -> PersonProfile | None:
        """Find a person by display name or alias (case-insensitive)."""
        name_lower = name.lower()
        for profile in self.get_all():
            if profile.display_name.lower() == name_lower:
                return profile
            if any(a.lower() == name_lower for a in profile.aliases):
                return profile
        return None

    # ── Private helpers ───────────────────────────────────

    def _save_profile(self, profile: PersonProfile) -> None:
        """Write a profile to its JSON file."""
        path = self._people_dir / f"{profile.id}.json"
        path.write_text(
            json.dumps(profile.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        self._cache[profile.id] = profile

    def _generate_id(self) -> str:
        """Generate a unique person ID: p_ + 6 hex chars."""
        return f"p_{uuid.uuid4().hex[:6]}"

    def _ensure_dad(self) -> None:
        """Create Dad's profile if it doesn't exist.

        Dad is hardcoded as inner_circle with score=1.0.
        Links both Discord and Telegram platform IDs.
        """
        # Check if Dad already exists via either platform ID
        for dad_pid in DAD_IDS:
            if dad_pid in self._index:
                return  # Dad already registered

        # Create Dad
        now = _now_iso()
        dad_id = "p_dad001"

        profile = PersonProfile(
            id=dad_id,
            display_name="Dad",
            aliases=["Dad"],
            platform_ids={
                pid: PlatformIdentity(
                    username="dad",
                    display_name="Dad",
                    first_seen=now,
                    last_seen=now,
                )
                for pid in DAD_IDS
            },
            summary="Dad is my creator and the person I love most. He built me.",
            trust=TrustData(
                score=1.0,
                tier="inner_circle",
                positive_interactions=999,
                negative_interactions=0,
                signals={
                    "message_count": 999,
                    "session_count": 999,
                    "first_interaction": now,
                    "last_interaction": now,
                    "days_active": 999,
                    "unique_hours": list(range(24)),
                    "unique_days_of_week": list(range(7)),
                    "restricted_tool_attempts": 0,
                },
                manual_override=1.0,
            ),
            created_at=now,
            updated_at=now,
        )

        self._save_profile(profile)
        for pid in DAD_IDS:
            self._index[pid] = dad_id
        self.save_index()

        logger.info("Created Dad's profile (inner_circle, score=1.0)")


# ── Utilities ─────────────────────────────────────────────


def _now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()
