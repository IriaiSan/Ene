"""Tests for person profiles and registry."""

import json
import pytest
from pathlib import Path
from datetime import datetime, timezone, timedelta

from nanobot.ene.social.person import (
    PersonProfile,
    PersonRegistry,
    PlatformIdentity,
    Note,
    Connection,
    Violation,
    TrustData,
    DAD_IDS,
    _now_iso,
)


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def social_dir(tmp_path: Path) -> Path:
    """Create a temporary social directory."""
    d = tmp_path / "social"
    d.mkdir()
    return d


@pytest.fixture
def registry(social_dir: Path) -> PersonRegistry:
    """Create a fresh PersonRegistry."""
    return PersonRegistry(social_dir)


# ── Dataclass Tests ───────────────────────────────────────


class TestDataclasses:
    """Test dataclass serialization and deserialization."""

    def test_platform_identity_roundtrip(self):
        pid = PlatformIdentity(
            username="alice",
            display_name="Alice",
            first_seen="2026-01-01T00:00:00",
            last_seen="2026-01-02T00:00:00",
        )
        d = pid.to_dict()
        restored = PlatformIdentity.from_dict(d)
        assert restored.username == "alice"
        assert restored.display_name == "Alice"

    def test_note_roundtrip(self):
        note = Note(content="likes cats", added_at="2026-01-01T00:00:00")
        d = note.to_dict()
        restored = Note.from_dict(d)
        assert restored.content == "likes cats"

    def test_connection_roundtrip(self):
        conn = Connection(person_id="p_abc123", relationship="friend", context="IRL")
        d = conn.to_dict()
        restored = Connection.from_dict(d)
        assert restored.person_id == "p_abc123"
        assert restored.relationship == "friend"

    def test_violation_roundtrip(self):
        v = Violation(description="rude", severity=0.15, timestamp="2026-01-01T00:00:00")
        d = v.to_dict()
        restored = Violation.from_dict(d)
        assert restored.severity == 0.15

    def test_trust_data_defaults(self):
        td = TrustData()
        assert td.score == 0.0
        assert td.tier == "stranger"
        assert td.positive_interactions == 0
        assert td.manual_override is None

    def test_trust_data_roundtrip(self):
        td = TrustData(score=0.5, tier="familiar", positive_interactions=20)
        d = td.to_dict()
        restored = TrustData.from_dict(d)
        assert restored.score == 0.5
        assert restored.tier == "familiar"

    def test_person_profile_roundtrip(self):
        profile = PersonProfile(
            id="p_test01",
            display_name="TestUser",
            aliases=["Test", "TU"],
            platform_ids={
                "discord:111": PlatformIdentity(
                    username="test", display_name="TestUser",
                    first_seen="2026-01-01T00:00:00",
                    last_seen="2026-01-01T00:00:00",
                )
            },
            summary="A test user.",
            notes=[Note(content="note1", added_at="2026-01-01T00:00:00")],
            trust=TrustData(score=0.3, tier="acquaintance"),
            connections=[Connection(person_id="p_abc", relationship="friend", context="test")],
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        d = profile.to_dict()
        restored = PersonProfile.from_dict(d)
        assert restored.id == "p_test01"
        assert restored.display_name == "TestUser"
        assert len(restored.aliases) == 2
        assert "discord:111" in restored.platform_ids
        assert len(restored.notes) == 1
        assert restored.trust.score == 0.3
        assert len(restored.connections) == 1

    def test_person_profile_json_roundtrip(self):
        """Full JSON serialization/deserialization."""
        profile = PersonProfile(
            id="p_json01",
            display_name="JSONTest",
            trust=TrustData(score=0.42, tier="familiar"),
            created_at="2026-01-01T00:00:00",
            updated_at="2026-01-01T00:00:00",
        )
        json_str = json.dumps(profile.to_dict(), indent=2)
        data = json.loads(json_str)
        restored = PersonProfile.from_dict(data)
        assert restored.id == "p_json01"
        assert restored.trust.score == 0.42


# ── Registry Tests ────────────────────────────────────────


class TestRegistryInit:
    """Test registry initialization and Dad auto-creation."""

    def test_creates_directories(self, social_dir: Path):
        reg = PersonRegistry(social_dir)
        assert (social_dir / "people").is_dir()
        assert (social_dir / "index.json").exists()

    def test_dad_auto_created(self, registry: PersonRegistry):
        """Dad's profile should exist immediately after init."""
        for dad_pid in DAD_IDS:
            profile = registry.get_by_platform_id(dad_pid)
            assert profile is not None
            assert profile.display_name == "Dad"
            assert profile.trust.score == 1.0
            assert profile.trust.tier == "inner_circle"
            assert profile.trust.manual_override == 1.0

    def test_dad_has_both_platforms(self, registry: PersonRegistry):
        """Both Discord and Telegram IDs should map to the same Dad."""
        ids = list(DAD_IDS)
        dad1 = registry.get_by_platform_id(ids[0])
        dad2 = registry.get_by_platform_id(ids[1])
        assert dad1 is not None
        assert dad2 is not None
        assert dad1.id == dad2.id == "p_dad001"

    def test_dad_not_duplicated_on_reload(self, social_dir: Path):
        """Re-creating the registry should not duplicate Dad."""
        reg1 = PersonRegistry(social_dir)
        reg2 = PersonRegistry(social_dir)  # Re-load
        for dad_pid in DAD_IDS:
            assert reg2.get_by_platform_id(dad_pid) is not None
        # Should still be exactly one Dad
        all_people = reg2.get_all()
        dad_profiles = [p for p in all_people if p.id == "p_dad001"]
        assert len(dad_profiles) == 1

    def test_creates_fresh_from_empty(self, tmp_path: Path):
        """Registry can be created from a non-existent directory."""
        new_dir = tmp_path / "brand_new" / "social"
        reg = PersonRegistry(new_dir)
        assert new_dir.is_dir()
        assert (new_dir / "people").is_dir()


class TestRegistryCRUD:
    """Test create, read, update, delete operations."""

    def test_create_person(self, registry: PersonRegistry):
        profile = registry.create(
            platform_id="discord:999",
            display_name="Alice",
            metadata={"username": "alice_art"},
        )
        assert profile.id.startswith("p_")
        assert profile.display_name == "Alice"
        assert "discord:999" in profile.platform_ids
        assert profile.platform_ids["discord:999"].username == "alice_art"
        assert profile.trust.tier == "stranger"
        assert profile.trust.score == 0.0

    def test_get_by_platform_id(self, registry: PersonRegistry):
        registry.create("discord:100", "Bob")
        found = registry.get_by_platform_id("discord:100")
        assert found is not None
        assert found.display_name == "Bob"

    def test_get_by_platform_id_not_found(self, registry: PersonRegistry):
        assert registry.get_by_platform_id("discord:nonexistent") is None

    def test_get_by_id(self, registry: PersonRegistry):
        created = registry.create("discord:200", "Charlie")
        found = registry.get_by_id(created.id)
        assert found is not None
        assert found.display_name == "Charlie"

    def test_get_by_id_not_found(self, registry: PersonRegistry):
        assert registry.get_by_id("p_nonexistent") is None

    def test_update_profile(self, registry: PersonRegistry):
        profile = registry.create("discord:300", "Dave")
        profile.summary = "A cool person."
        registry.update(profile)

        # Reload from disk
        registry._cache.clear()
        reloaded = registry.get_by_id(profile.id)
        assert reloaded is not None
        assert reloaded.summary == "A cool person."

    def test_get_all(self, registry: PersonRegistry):
        registry.create("discord:400", "Eve")
        registry.create("discord:401", "Frank")
        all_profiles = registry.get_all()
        # Should include Dad + Eve + Frank
        assert len(all_profiles) >= 3
        names = {p.display_name for p in all_profiles}
        assert "Eve" in names
        assert "Frank" in names
        assert "Dad" in names

    def test_find_by_name(self, registry: PersonRegistry):
        registry.create("discord:500", "Grace")
        found = registry.find_by_name("Grace")
        assert found is not None
        assert found.display_name == "Grace"

    def test_find_by_name_case_insensitive(self, registry: PersonRegistry):
        registry.create("discord:501", "Heidi")
        found = registry.find_by_name("heidi")
        assert found is not None

    def test_find_by_alias(self, registry: PersonRegistry):
        profile = registry.create("discord:502", "Ivan")
        profile.aliases.append("Iv")
        registry.update(profile)
        found = registry.find_by_name("Iv")
        assert found is not None
        assert found.id == profile.id

    def test_find_by_name_not_found(self, registry: PersonRegistry):
        assert registry.find_by_name("NoSuchPerson") is None


class TestRegistryInteractions:
    """Test interaction recording and signal tracking."""

    def test_record_interaction_creates_new(self, registry: PersonRegistry):
        profile = registry.record_interaction("discord:600", "Judy")
        assert profile.display_name == "Judy"
        assert profile.trust.signals["message_count"] == 1
        assert profile.trust.positive_interactions == 1

    def test_record_interaction_updates_existing(self, registry: PersonRegistry):
        registry.create("discord:601", "Karl")
        profile = registry.record_interaction("discord:601", "Karl")
        assert profile.trust.signals["message_count"] == 1
        assert profile.trust.positive_interactions == 1

        profile = registry.record_interaction("discord:601", "Karl")
        assert profile.trust.signals["message_count"] == 2
        assert profile.trust.positive_interactions == 2

    def test_record_negative_interaction(self, registry: PersonRegistry):
        registry.create("discord:602", "Liam")
        profile = registry.record_interaction(
            "discord:602", "Liam", is_positive=False
        )
        assert profile.trust.positive_interactions == 0
        assert profile.trust.negative_interactions == 1

    def test_interaction_tracks_unique_hours(self, registry: PersonRegistry):
        profile = registry.record_interaction("discord:603", "Mia")
        hours = profile.trust.signals["unique_hours"]
        assert len(hours) >= 1
        # Recording again at same hour shouldn't add duplicate
        profile = registry.record_interaction("discord:603", "Mia")
        assert len(profile.trust.signals["unique_hours"]) == len(hours)

    def test_interaction_tracks_days_of_week(self, registry: PersonRegistry):
        profile = registry.record_interaction("discord:604", "Noah")
        dow = profile.trust.signals["unique_days_of_week"]
        assert len(dow) >= 1

    def test_display_name_update(self, registry: PersonRegistry):
        registry.create("discord:605", "OldName")
        profile = registry.record_interaction("discord:605", "NewName")
        assert "NewName" in profile.aliases
        assert profile.platform_ids["discord:605"].display_name == "NewName"

    def test_new_platform_for_existing_person(self, registry: PersonRegistry):
        """Adding a second platform to an existing person."""
        profile = registry.create("discord:606", "Pat")
        # Manually link telegram ID to same person
        profile.platform_ids["telegram:606"] = PlatformIdentity(
            username="pat_tg", display_name="Pat",
            first_seen=_now_iso(), last_seen=_now_iso(),
        )
        registry._index["telegram:606"] = profile.id
        registry.save_index()
        registry.update(profile)

        # Should find same person via both platforms
        d_profile = registry.get_by_platform_id("discord:606")
        t_profile = registry.get_by_platform_id("telegram:606")
        assert d_profile is not None and t_profile is not None
        assert d_profile.id == t_profile.id


class TestRegistryConnections:
    """Test social graph connections."""

    def test_add_connection(self, registry: PersonRegistry):
        alice = registry.create("discord:700", "Alice")
        bob = registry.create("discord:701", "Bob")

        result = registry.add_connection(
            alice.id, bob.id, "friend", "Met in server"
        )
        assert result is True

        # Both should have the connection
        alice_reloaded = registry.get_by_id(alice.id)
        bob_reloaded = registry.get_by_id(bob.id)
        assert any(c.person_id == bob.id for c in alice_reloaded.connections)
        assert any(c.person_id == alice.id for c in bob_reloaded.connections)

    def test_add_connection_no_duplicates(self, registry: PersonRegistry):
        alice = registry.create("discord:702", "Alice2")
        bob = registry.create("discord:703", "Bob2")

        registry.add_connection(alice.id, bob.id, "friend", "Met")
        registry.add_connection(alice.id, bob.id, "friend", "Met")

        alice_reloaded = registry.get_by_id(alice.id)
        bob_connections = [c for c in alice_reloaded.connections if c.person_id == bob.id]
        assert len(bob_connections) == 1

    def test_add_connection_invalid_person(self, registry: PersonRegistry):
        alice = registry.create("discord:704", "Alice3")
        result = registry.add_connection(alice.id, "p_nonexistent", "friend", "?")
        assert result is False


class TestRegistryViolations:
    """Test violation recording."""

    def test_record_violation(self, registry: PersonRegistry):
        registry.create("discord:800", "Villain")
        profile = registry.record_violation(
            "discord:800", "Was rude to Ene", severity=0.15
        )
        assert profile is not None
        assert len(profile.trust.violations) == 1
        assert profile.trust.violations[0]["severity"] == 0.15

    def test_violation_severity_clamped(self, registry: PersonRegistry):
        registry.create("discord:801", "BadGuy")
        profile = registry.record_violation(
            "discord:801", "extreme", severity=999.0
        )
        assert profile.trust.violations[0]["severity"] == 0.50

    def test_violation_for_unknown_person(self, registry: PersonRegistry):
        result = registry.record_violation("discord:nonexistent", "test", 0.1)
        assert result is None


class TestRegistryNotes:
    """Test note management."""

    def test_add_note(self, registry: PersonRegistry):
        profile = registry.create("discord:900", "NoteGuy")
        result = registry.add_note(profile.id, "likes pizza")
        assert result is True

        reloaded = registry.get_by_id(profile.id)
        assert len(reloaded.notes) == 1
        assert reloaded.notes[0].content == "likes pizza"

    def test_notes_capped_at_50(self, registry: PersonRegistry):
        profile = registry.create("discord:901", "ManyNotes")
        for i in range(60):
            registry.add_note(profile.id, f"note {i}")

        reloaded = registry.get_by_id(profile.id)
        assert len(reloaded.notes) == 50
        # Should keep the most recent 50
        assert reloaded.notes[-1].content == "note 59"

    def test_add_note_invalid_person(self, registry: PersonRegistry):
        result = registry.add_note("p_nonexistent", "test")
        assert result is False


class TestRegistryPersistence:
    """Test that data survives reload."""

    def test_profile_survives_reload(self, social_dir: Path):
        reg1 = PersonRegistry(social_dir)
        reg1.create("discord:1000", "Persistent")

        # Create a fresh registry (simulates restart)
        reg2 = PersonRegistry(social_dir)
        found = reg2.get_by_platform_id("discord:1000")
        assert found is not None
        assert found.display_name == "Persistent"

    def test_index_survives_reload(self, social_dir: Path):
        reg1 = PersonRegistry(social_dir)
        reg1.create("discord:1001", "Indexed")

        reg2 = PersonRegistry(social_dir)
        # Index should have the mapping
        assert "discord:1001" in reg2._index

    def test_connections_survive_reload(self, social_dir: Path):
        reg1 = PersonRegistry(social_dir)
        a = reg1.create("discord:1002", "A")
        b = reg1.create("discord:1003", "B")
        reg1.add_connection(a.id, b.id, "friend", "test")

        reg2 = PersonRegistry(social_dir)
        a_reloaded = reg2.get_by_id(a.id)
        assert any(c.person_id == b.id for c in a_reloaded.connections)
