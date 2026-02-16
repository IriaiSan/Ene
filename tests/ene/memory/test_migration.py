"""Tests for memory migration — v1 to v2."""

import pytest
from pathlib import Path

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
    return ws


@pytest.fixture
def chroma_client():
    """Fresh in-memory ChromaDB client."""
    client = chromadb.Client()
    for col in client.list_collections():
        client.delete_collection(col.name)
    return client


def _create_system(workspace: Path, chroma_client, token_budget: int = 4000) -> MemorySystem:
    """Helper to create a MemorySystem with test components."""
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)
    (memory_dir / "diary").mkdir(exist_ok=True)

    sys = MemorySystem(workspace=workspace, token_budget=token_budget)
    sys._memory_dir = memory_dir
    sys._diary_dir = memory_dir / "diary"
    sys._core = CoreMemory(memory_dir, token_budget=token_budget)
    sys._vector = VectorMemory(client=chroma_client)
    return sys


# ── Migration from MEMORY.md ──────────────────────────────


def test_migrate_from_memory_md(workspace: Path, chroma_client):
    """MEMORY.md should be migrated to core.json."""
    # Create legacy MEMORY.md
    memory_md = workspace / "MEMORY.md"
    memory_md.write_text(
        "# Identity\n"
        "- I'm Ene.\n"
        "- Dad built me.\n"
        "\n"
        "# People\n"
        "- CCC is an artist.\n"
        "- Dad is my creator.\n",
        encoding="utf-8",
    )

    sys = _create_system(workspace, chroma_client)
    sys._maybe_migrate()

    # Entries should be in core memory
    all_entries = sys.core.get_all_entries()
    assert len(all_entries) >= 4

    # Check some entries exist
    contents = [e["content"] for _, e in all_entries]
    assert "I'm Ene." in contents
    assert "Dad built me." in contents


def test_migrate_from_core_md(workspace: Path, chroma_client):
    """CORE.md should also trigger migration."""
    core_md = workspace / "CORE.md"
    core_md.write_text(
        "- First fact.\n"
        "- Second fact.\n",
        encoding="utf-8",
    )

    sys = _create_system(workspace, chroma_client)
    sys._maybe_migrate()

    all_entries = sys.core.get_all_entries()
    assert len(all_entries) >= 2


def test_migrate_from_memory_subdir(workspace: Path, chroma_client):
    """MEMORY.md in memory/ subdirectory should also be found."""
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)
    memory_md = memory_dir / "MEMORY.md"
    memory_md.write_text("- Test entry from subdirectory.\n", encoding="utf-8")

    sys = _create_system(workspace, chroma_client)
    sys._maybe_migrate()

    all_entries = sys.core.get_all_entries()
    assert len(all_entries) >= 1


def test_migrate_backup_files(workspace: Path, chroma_client):
    """Migration should rename legacy files to .bak."""
    memory_md = workspace / "MEMORY.md"
    memory_md.write_text("- Test entry.\n", encoding="utf-8")

    sys = _create_system(workspace, chroma_client)
    sys._maybe_migrate()

    assert not memory_md.exists()
    assert (workspace / "MEMORY.md.bak").exists()


def test_migrate_idempotent(workspace: Path, chroma_client):
    """Running migration twice should not duplicate entries."""
    memory_md = workspace / "MEMORY.md"
    memory_md.write_text("- Test entry.\n", encoding="utf-8")

    sys = _create_system(workspace, chroma_client)
    sys._maybe_migrate()

    count_after_first = len(sys.core.get_all_entries())

    # Second run — core.json already exists so migration should skip
    sys._maybe_migrate()

    count_after_second = len(sys.core.get_all_entries())
    assert count_after_first == count_after_second


def test_migrate_over_budget(workspace: Path, chroma_client):
    """Entries exceeding core budget should be archived to vector store."""
    # Create many entries that will exceed a tiny budget
    lines = [f"- Entry number {i} with some extra text to use tokens.\n" for i in range(50)]
    memory_md = workspace / "MEMORY.md"
    memory_md.write_text("".join(lines), encoding="utf-8")

    # Use tiny budget so most entries overflow
    sys = _create_system(workspace, chroma_client, token_budget=100)
    sys._maybe_migrate()

    # Some should be in core, some archived
    core_count = len(sys.core.get_all_entries())
    vector_count = sys.vector.get_memory_count()

    assert core_count > 0
    assert vector_count > 0
    assert core_count + vector_count == 50


def test_migrate_section_detection(workspace: Path, chroma_client):
    """Migration should detect section headers and assign entries accordingly."""
    memory_md = workspace / "MEMORY.md"
    memory_md.write_text(
        "# Who I Am / Identity\n"
        "- I'm an AI companion.\n"
        "\n"
        "# People I Know\n"
        "- Dad is my creator.\n"
        "\n"
        "# My Preferences\n"
        "- I like creative conversations.\n",
        encoding="utf-8",
    )

    sys = _create_system(workspace, chroma_client)
    sys._maybe_migrate()

    entries = sys.core.get_all_entries()
    sections = {sec for sec, _ in entries}

    # Should have detected identity, people, and preferences sections
    assert "identity" in sections
    assert "people" in sections
    assert "preferences" in sections


def test_migrate_diary_files(workspace: Path, chroma_client):
    """Existing diary files should be indexed into vector store."""
    # Create diary files
    diary_dir = workspace / "memory" / "diary"
    diary_dir.mkdir(parents=True, exist_ok=True)
    (diary_dir / "2026-02-14.md").write_text("Valentine's Day was fun.", encoding="utf-8")
    (diary_dir / "2026-02-15.md").write_text("Worked on memory system.", encoding="utf-8")

    # Create a legacy file to trigger migration
    memory_md = workspace / "MEMORY.md"
    memory_md.write_text("- Trigger migration.\n", encoding="utf-8")

    sys = _create_system(workspace, chroma_client)
    sys._maybe_migrate()

    # Diary files should be indexed (2 diary + some from migration overflow)
    # At minimum, the 2 diary entries should be there
    assert sys.vector.get_memory_count() >= 2


def test_no_migration_when_core_exists(workspace: Path, chroma_client):
    """Migration should not run when core.json already exists."""
    # Create core.json first
    memory_dir = workspace / "memory"
    memory_dir.mkdir(exist_ok=True)
    core_json = memory_dir / "core.json"
    core_json.write_text("{}", encoding="utf-8")

    # Also create a MEMORY.md (should be ignored)
    memory_md = workspace / "MEMORY.md"
    memory_md.write_text("- Should not be migrated.\n", encoding="utf-8")

    sys = _create_system(workspace, chroma_client)
    sys._maybe_migrate()

    # MEMORY.md should still exist (not renamed)
    assert memory_md.exists()


def test_no_migration_no_legacy(workspace: Path, chroma_client):
    """No migration should run if there are no legacy files."""
    sys = _create_system(workspace, chroma_client)
    sys._maybe_migrate()

    # core.json should not exist (no entries added)
    core_json = workspace / "memory" / "core.json"
    assert not core_json.exists()
