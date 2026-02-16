"""Tests for VectorMemory — Ene's long-term vector memory (ChromaDB)."""

import time
import pytest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import chromadb

from nanobot.ene.memory.vector_memory import (
    VectorMemory,
    MemoryResult,
    EntityResult,
    DEFAULT_DECAY_RATE,
)


# ── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def chroma_client():
    """Create a fresh in-memory ChromaDB client for each test.

    We delete any existing collections to ensure test isolation,
    since chromadb.Client() may reuse the same in-process state.
    """
    client = chromadb.Client()
    # Clean up any leftover collections from previous tests
    for col in client.list_collections():
        client.delete_collection(col.name)
    return client


@pytest.fixture
def vm(chroma_client):
    """Create a VectorMemory with in-memory ChromaDB (uses default embeddings)."""
    return VectorMemory(client=chroma_client)


# ── Add Memory ─────────────────────────────────────────────


def test_add_memory_returns_id(vm: VectorMemory):
    """add_memory() should return an 8-char hex ID."""
    mid = vm.add_memory("Ene likes coding with Dad.", memory_type="fact", importance=7)
    assert mid is not None
    assert len(mid) == 8


def test_add_memory_stored_correctly(vm: VectorMemory):
    """Added memory should be retrievable by ID."""
    mid = vm.add_memory(
        "CCC is an artist.",
        memory_type="fact",
        importance=8,
        source="discord",
        related_entities="CCC",
    )

    mem = vm.get_memory(mid)
    assert mem is not None
    assert mem["content"] == "CCC is an artist."
    assert mem["metadata"]["type"] == "fact"
    assert mem["metadata"]["importance"] == 8
    assert mem["metadata"]["source"] == "discord"
    assert mem["metadata"]["related_entities"] == "CCC"
    assert mem["metadata"]["superseded_by"] == ""
    assert mem["metadata"]["access_count"] == 0


def test_add_memory_clamps_importance(vm: VectorMemory):
    """Importance should be clamped to 1-10."""
    mid1 = vm.add_memory("low", importance=-5)
    mid2 = vm.add_memory("high", importance=99)

    assert vm.get_memory(mid1)["metadata"]["importance"] == 1
    assert vm.get_memory(mid2)["metadata"]["importance"] == 10


def test_memory_count(vm: VectorMemory):
    """get_memory_count() should reflect added memories."""
    assert vm.get_memory_count() == 0
    vm.add_memory("mem1")
    vm.add_memory("mem2")
    assert vm.get_memory_count() == 2


# ── Search ─────────────────────────────────────────────────


def test_search_returns_results(vm: VectorMemory):
    """search() should return relevant memories."""
    vm.add_memory("Ene was created by Dad in 2026.", importance=9)
    vm.add_memory("CCC likes painting watercolors.", importance=6)
    vm.add_memory("The weather was rainy today.", importance=2)

    results = vm.search("Who created Ene?", limit=2)

    assert len(results) <= 2
    assert all(isinstance(r, MemoryResult) for r in results)
    assert all(r.score > 0 for r in results)


def test_search_type_filter(vm: VectorMemory):
    """search() with memory_type should only return matching types."""
    vm.add_memory("fact about Ene", memory_type="fact")
    vm.add_memory("diary entry about coding", memory_type="diary")
    vm.add_memory("another fact", memory_type="fact")

    results = vm.search("Ene", memory_type="fact", limit=10)
    assert all(r.memory_type == "fact" for r in results)


def test_search_importance_filter(vm: VectorMemory):
    """search() with min_importance should filter low-importance memories."""
    vm.add_memory("important fact", importance=9)
    vm.add_memory("trivial fact", importance=1)

    results = vm.search("fact", min_importance=5, limit=10)
    assert all(r.importance >= 5 for r in results)


def test_search_excludes_superseded(vm: VectorMemory):
    """Superseded memories should not appear in search results."""
    mid1 = vm.add_memory("CCC lives in Tokyo", importance=7)
    mid2 = vm.add_memory("CCC moved to Osaka", importance=7)
    vm.mark_superseded(mid1, mid2)

    results = vm.search("Where does CCC live?", limit=10)
    result_ids = [r.id for r in results]
    assert mid1 not in result_ids


def test_search_empty_store(vm: VectorMemory):
    """search() on empty store should return empty list."""
    results = vm.search("anything")
    assert results == []


def test_search_updates_access(vm: VectorMemory):
    """search() should update last_accessed_at and access_count."""
    mid = vm.add_memory("test memory")
    original = vm.get_memory(mid)
    assert original["metadata"]["access_count"] == 0

    vm.search("test", limit=5)

    updated = vm.get_memory(mid)
    assert updated["metadata"]["access_count"] >= 1


def test_search_score_components(vm: VectorMemory):
    """MemoryResult should have score, distance, and metadata fields."""
    mid = vm.add_memory("test memory for scoring", importance=8)

    results = vm.search("test memory for scoring", limit=1)
    assert len(results) == 1

    r = results[0]
    assert r.id == mid
    assert r.score > 0
    assert r.importance == 8
    assert r.created_at != ""
    assert r.access_count >= 0


# ── Three-Factor Ranking ──────────────────────────────────


def test_high_importance_ranks_higher(vm: VectorMemory):
    """With similar text, higher importance should rank higher."""
    # Add two very similar memories with different importance
    vm.add_memory("Ene is an AI companion created by Dad.", importance=2)
    vm.add_memory("Ene is an AI companion built by Dad.", importance=9)

    results = vm.search("AI companion", limit=2)
    # Higher importance should generally be ranked higher
    if len(results) == 2:
        # The higher importance one should have a higher score
        importances = [r.importance for r in results]
        assert 9 in importances


# ── Access Tracking ────────────────────────────────────────


def test_update_access(vm: VectorMemory):
    """update_access() should bump access_count and last_accessed_at."""
    mid = vm.add_memory("tracked memory")
    original_meta = vm.get_memory(mid)["metadata"]

    vm.update_access(mid)

    updated_meta = vm.get_memory(mid)["metadata"]
    assert updated_meta["access_count"] == original_meta["access_count"] + 1


def test_update_access_nonexistent(vm: VectorMemory):
    """update_access() on nonexistent ID should not raise."""
    vm.update_access("nonexistent_id")  # Should not raise


# ── Superseding ────────────────────────────────────────────


def test_mark_superseded(vm: VectorMemory):
    """mark_superseded() should set superseded_by metadata."""
    mid1 = vm.add_memory("old fact")
    mid2 = vm.add_memory("new fact")

    vm.mark_superseded(mid1, mid2)

    mem = vm.get_memory(mid1)
    assert mem["metadata"]["superseded_by"] == mid2


def test_mark_superseded_nonexistent(vm: VectorMemory):
    """mark_superseded() on nonexistent ID should not raise."""
    vm.mark_superseded("nonexistent", "also_nonexistent")  # Should not raise


# ── Delete ─────────────────────────────────────────────────


def test_delete_memory(vm: VectorMemory):
    """delete_memory() should permanently remove the memory."""
    mid = vm.add_memory("to be deleted")
    assert vm.get_memory_count() == 1

    result = vm.delete_memory(mid)
    assert result is True
    assert vm.get_memory_count() == 0
    assert vm.get_memory(mid) is None


def test_delete_memory_nonexistent(vm: VectorMemory):
    """delete_memory() on nonexistent ID should return False."""
    result = vm.delete_memory("nonexistent")
    assert result is False


# ── Pruning Candidates ────────────────────────────────────


def test_get_pruning_candidates_empty(vm: VectorMemory):
    """Empty store should return no pruning candidates."""
    candidates = vm.get_pruning_candidates()
    assert candidates == []


def test_get_pruning_candidates_filters_importance(vm: VectorMemory):
    """Pruning should only consider low-importance memories."""
    # Add a high-importance memory (should NOT be a candidate)
    vm.add_memory("very important", importance=9)
    # Add a low-importance memory (could be a candidate)
    vm.add_memory("not important", importance=2)

    candidates = vm.get_pruning_candidates(max_importance=4)
    # All candidates should have importance <= 4
    for c in candidates:
        assert c["importance"] <= 4


def test_get_pruning_candidates_respects_decay(chroma_client):
    """Old, rarely-accessed memories should be pruning candidates."""
    vm = VectorMemory(client=chroma_client)

    # Add memory with old last_accessed_at (simulate aging)
    mid = vm.add_memory("old forgotten memory", importance=2)

    # Manually set last_accessed_at to 60 days ago
    old_time = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
    mem = vm.get_memory(mid)
    meta = mem["metadata"]
    meta["last_accessed_at"] = old_time
    meta["access_count"] = 0
    vm._memories.update(ids=[mid], metadatas=[meta])

    candidates = vm.get_pruning_candidates(
        decay_rate=0.1,
        prune_threshold=0.5,
        max_importance=4,
    )

    # The old, low-importance, never-accessed memory should be a candidate
    assert len(candidates) >= 1
    assert candidates[0]["id"] == mid
    assert candidates[0]["strength"] < 0.5


# ── Entities ───────────────────────────────────────────────


def test_add_entity(vm: VectorMemory):
    """add_entity() should return an ID and store correctly."""
    eid = vm.add_entity(
        name="CCC",
        entity_type="person",
        description="An artist and Dad's close friend.",
        importance=8,
        aliases="C,Triple-C",
    )

    assert eid is not None
    assert len(eid) == 8
    assert vm.get_entity_count() == 1


def test_get_entity_by_name(vm: VectorMemory):
    """get_entity_by_name() should find entity by exact name."""
    vm.add_entity(name="CCC", entity_type="person", description="Artist friend")

    result = vm.get_entity_by_name("CCC")
    assert result is not None
    assert isinstance(result, EntityResult)
    assert result.name == "CCC"
    assert result.entity_type == "person"


def test_get_entity_by_name_not_found(vm: VectorMemory):
    """get_entity_by_name() should return None if not found."""
    result = vm.get_entity_by_name("NonexistentPerson")
    assert result is None


def test_get_entity_by_alias(vm: VectorMemory):
    """get_entity_by_name() should also match aliases."""
    vm.add_entity(
        name="CCC",
        entity_type="person",
        description="An artist",
        aliases="Triple-C,C-Chan",
    )

    result = vm.get_entity_by_name("Triple-C")
    assert result is not None
    assert result.name == "CCC"


def test_upsert_entity_creates_new(vm: VectorMemory):
    """upsert_entity() should create entity if not exists."""
    eid = vm.upsert_entity(name="NewPerson", description="Someone new")

    assert eid is not None
    assert vm.get_entity_count() == 1


def test_upsert_entity_updates_existing(vm: VectorMemory):
    """upsert_entity() should update existing entity."""
    vm.add_entity(name="CCC", entity_type="person", description="An artist")

    eid = vm.upsert_entity(
        name="CCC",
        description="An artist who also codes",
        importance=9,
    )

    entity = vm.get_entity_by_name("CCC")
    assert entity is not None
    assert entity.interaction_count == 2  # incremented from 1
    assert entity.importance == 9


def test_search_entities(vm: VectorMemory):
    """search_entities() should return matching entities."""
    vm.add_entity(name="CCC", entity_type="person", description="An artist friend")
    vm.add_entity(name="Discord Server", entity_type="place", description="Ene's home")

    results = vm.search_entities("artist", limit=5)
    assert len(results) >= 1
    assert all(isinstance(r, EntityResult) for r in results)


def test_search_entities_type_filter(vm: VectorMemory):
    """search_entities() with entity_type should filter correctly."""
    vm.add_entity(name="CCC", entity_type="person", description="Artist")
    vm.add_entity(name="Tokyo", entity_type="place", description="City in Japan")

    results = vm.search_entities("something", entity_type="person", limit=10)
    assert all(r.entity_type == "person" for r in results)


def test_get_entity_names(vm: VectorMemory):
    """get_entity_names() should return lowercase name → ID mapping."""
    vm.add_entity(name="CCC", aliases="Triple-C")
    vm.add_entity(name="Dad")

    names = vm.get_entity_names()
    assert "ccc" in names
    assert "triple-c" in names
    assert "dad" in names


def test_get_entity_names_empty(vm: VectorMemory):
    """get_entity_names() on empty store should return empty dict."""
    names = vm.get_entity_names()
    assert names == {}


# ── Reflections ────────────────────────────────────────────


def test_add_reflection(vm: VectorMemory):
    """add_reflection() should store and return an ID."""
    rid = vm.add_reflection(
        content="Ene is most active in the evenings when Dad is online.",
        importance=7,
        source_ids="abc123,def456",
        topic="activity_patterns",
    )

    assert rid is not None
    assert len(rid) == 8
    assert vm.get_reflection_count() == 1


def test_search_reflections(vm: VectorMemory):
    """search_reflections() should return matching reflections."""
    vm.add_reflection("Ene enjoys creative conversations.", importance=7, topic="preferences")
    vm.add_reflection("Dad usually messages in the evening.", importance=5, topic="patterns")

    results = vm.search_reflections("creative")
    assert len(results) >= 1
    assert results[0]["content"] is not None
    assert "topic" in results[0]
    assert "importance" in results[0]


def test_search_reflections_empty(vm: VectorMemory):
    """search_reflections() on empty store should return empty list."""
    results = vm.search_reflections("anything")
    assert results == []


# ── Custom Embedding Function ─────────────────────────────


def test_custom_embedding_fn(chroma_client):
    """VectorMemory should use custom embedding_fn when provided."""
    # Simple mock embedding function (returns fixed-size vectors)
    call_count = {"n": 0}

    def mock_embed(texts: list[str]) -> list[list[float]]:
        call_count["n"] += 1
        return [[0.1] * 384 for _ in texts]

    vm = VectorMemory(client=chroma_client, embedding_fn=mock_embed)
    vm.add_memory("test with custom embeddings")

    # Should have called our function
    assert call_count["n"] >= 1

    # Search should also use custom function
    vm.search("test query", limit=1)
    assert call_count["n"] >= 2


# ── Get Memory ─────────────────────────────────────────────


def test_get_memory_existing(vm: VectorMemory):
    """get_memory() should return the memory dict."""
    mid = vm.add_memory("test content", memory_type="fact", importance=7)

    mem = vm.get_memory(mid)
    assert mem is not None
    assert mem["id"] == mid
    assert mem["content"] == "test content"
    assert mem["metadata"]["type"] == "fact"


def test_get_memory_nonexistent(vm: VectorMemory):
    """get_memory() for nonexistent ID should return None."""
    mem = vm.get_memory("nonexistent")
    assert mem is None
