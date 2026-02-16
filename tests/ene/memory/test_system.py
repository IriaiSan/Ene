"""Tests for MemorySystem — facade coordinating all memory subsystems."""

import pytest
from pathlib import Path
from datetime import datetime, timedelta

import chromadb

from nanobot.ene.memory.system import MemorySystem
from nanobot.ene.memory.core_memory import CoreMemory
from nanobot.ene.memory.vector_memory import VectorMemory


# ── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "memory").mkdir()
    (ws / "memory" / "diary").mkdir()
    return ws


@pytest.fixture
def chroma_client():
    """Fresh in-memory ChromaDB client."""
    client = chromadb.Client()
    for col in client.list_collections():
        client.delete_collection(col.name)
    return client


@pytest.fixture
def system(workspace: Path, chroma_client) -> MemorySystem:
    """Create a MemorySystem with test components."""
    sys = MemorySystem(workspace=workspace, token_budget=4000)
    sys._core = CoreMemory(workspace / "memory", token_budget=4000)
    sys._vector = VectorMemory(client=chroma_client)
    return sys


# ── Initialization ─────────────────────────────────────────


def test_initialize_creates_dirs(tmp_path: Path):
    """initialize() should create memory and diary directories."""
    ws = tmp_path / "new_workspace"
    ws.mkdir()
    sys = MemorySystem(workspace=ws, token_budget=4000)

    # Use a mock chroma_path to avoid actual disk ChromaDB
    sys._chroma_path = str(tmp_path / "chroma_test")
    sys.initialize()

    assert (ws / "memory").exists()
    assert (ws / "memory" / "diary").exists()
    assert sys.core is not None


def test_core_property_before_init(tmp_path: Path):
    """Accessing core before initialize should raise RuntimeError."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    sys = MemorySystem(workspace=ws)

    with pytest.raises(RuntimeError, match="not initialized"):
        _ = sys.core


# ── Memory Context ─────────────────────────────────────────


def test_get_memory_context_empty(system: MemorySystem):
    """Empty system should return core memory header."""
    context = system.get_memory_context()
    assert "Core Memory" in context
    assert "0/4000" in context


def test_get_memory_context_with_entries(system: MemorySystem):
    """Context should include core memory entries."""
    system.core.add_entry("identity", "I'm Ene.", importance=10)
    system.core.add_entry("people", "Dad is my creator.", importance=9)

    context = system.get_memory_context()

    assert "Who I Am" in context
    assert "I'm Ene." in context
    assert "People I Know" in context
    assert "Dad is my creator." in context


def test_get_memory_context_with_diary(system: MemorySystem, workspace: Path):
    """Context should include recent diary entries."""
    today = datetime.now().date().isoformat()
    diary_file = workspace / "memory" / "diary" / f"{today}.md"
    diary_file.write_text("Today was a good day. I learned Python.", encoding="utf-8")

    context = system.get_memory_context()

    assert "Recent Diary" in context
    assert today in context


def test_get_memory_context_skips_old_diary(system: MemorySystem, workspace: Path):
    """Diary entries older than diary_context_days should not appear."""
    old_date = (datetime.now().date() - timedelta(days=10)).isoformat()
    diary_file = workspace / "memory" / "diary" / f"{old_date}.md"
    diary_file.write_text("Old diary entry.", encoding="utf-8")

    context = system.get_memory_context()

    # Old entry should NOT appear (default 3 days)
    assert "Old diary entry" not in context


# ── Relevant Context (Retrieval) ──────────────────────────


def test_get_relevant_context_with_memories(system: MemorySystem):
    """Relevant context should include matching vector memories."""
    system.vector.add_memory("Ene was created in 2026.", memory_type="fact", importance=8)

    context = system.get_relevant_context("When was Ene created?")

    assert "Retrieved Memories" in context


def test_get_relevant_context_empty(system: MemorySystem):
    """Empty vector store should return empty context."""
    context = system.get_relevant_context("random question")
    assert context == ""


def test_get_relevant_context_no_vector(workspace: Path):
    """System without vector store should return empty context."""
    sys = MemorySystem(workspace=workspace)
    sys._core = CoreMemory(workspace / "memory", token_budget=4000)
    sys._vector = None

    context = sys.get_relevant_context("test")
    assert context == ""


# ── Entity Context ─────────────────────────────────────────


def test_entity_context_injection(system: MemorySystem):
    """Mentioning a known entity should inject their context."""
    system.vector.add_entity(
        name="CCC",
        entity_type="person",
        description="An artist and close friend",
    )
    system.invalidate_entity_cache()

    context = system.get_relevant_context("I talked to CCC today")

    assert "Entity Context" in context
    assert "CCC" in context


def test_entity_cache_refresh(system: MemorySystem):
    """Entity cache should refresh when invalidated."""
    # Add entity
    system.vector.add_entity(name="TestPerson", entity_type="person", description="Test")
    system.invalidate_entity_cache()

    # Should find entity in message
    context = system.get_relevant_context("Tell testperson about this")
    assert "TestPerson" in context


# ── Diary Writing ──────────────────────────────────────────


def test_write_diary_entry(system: MemorySystem, workspace: Path):
    """write_diary_entry should append to today's diary file."""
    system.write_diary_entry("Learned about memory systems today.")

    today = datetime.now().date().isoformat()
    diary_file = workspace / "memory" / "diary" / f"{today}.md"
    assert diary_file.exists()
    content = diary_file.read_text(encoding="utf-8")
    assert "Learned about memory systems" in content


def test_write_diary_entry_appends(system: MemorySystem, workspace: Path):
    """Multiple diary entries should append, not overwrite."""
    system.write_diary_entry("First entry.")
    system.write_diary_entry("Second entry.")

    today = datetime.now().date().isoformat()
    diary_file = workspace / "memory" / "diary" / f"{today}.md"
    content = diary_file.read_text(encoding="utf-8")
    assert "First entry" in content
    assert "Second entry" in content
