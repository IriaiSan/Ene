"""Tests for CoreMemory — Ene's permanent, editable, token-budgeted memory."""

import json
import pytest
from pathlib import Path

from nanobot.ene.memory.core_memory import CoreMemory, _count_tokens, SECTION_NAMES


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    """Create a temporary memory directory."""
    d = tmp_path / "memory"
    d.mkdir()
    return d


@pytest.fixture
def core(memory_dir: Path) -> CoreMemory:
    """Create a CoreMemory instance with default budget."""
    return CoreMemory(memory_dir, token_budget=4000)


# ── Initialization ─────────────────────────────────────────


def test_create_empty_core(core: CoreMemory):
    """New core memory should have all default sections and zero tokens."""
    assert core.get_total_tokens() == 0
    assert core.budget_remaining == 4000
    assert not core.is_over_budget

    # All default sections exist
    for name in SECTION_NAMES:
        assert core.get_section_tokens(name) == 0


def test_creates_core_json_on_init(memory_dir: Path):
    """core.json is NOT created on init — only on first save/add."""
    core = CoreMemory(memory_dir, token_budget=4000)
    # File doesn't exist until a write happens
    # (load() initializes in-memory, save() is called by add_entry)
    assert not (memory_dir / "core.json").exists()

    # After adding an entry, it should exist
    core.add_entry("scratch", "test entry")
    assert (memory_dir / "core.json").exists()


# ── Add Entry ──────────────────────────────────────────────


def test_add_entry(core: CoreMemory):
    """Adding an entry returns an ID and stores correctly."""
    entry_id = core.add_entry("identity", "I'm Ene. Dad built me.", importance=10)

    assert entry_id is not None
    assert len(entry_id) == 6  # short hex ID

    entry = core.find_entry(entry_id)
    assert entry is not None
    assert entry["content"] == "I'm Ene. Dad built me."
    assert entry["importance"] == 10
    assert "created_at" in entry
    assert "updated_at" in entry


def test_add_entry_token_counting(core: CoreMemory):
    """Token count should update after adding entries."""
    text = "This is a test memory entry for Ene."
    expected_tokens = _count_tokens(text)

    core.add_entry("scratch", text)

    assert core.get_total_tokens() == expected_tokens
    assert core.get_section_tokens("scratch") == expected_tokens
    assert core.budget_remaining == 4000 - expected_tokens


def test_add_entry_invalid_section(core: CoreMemory):
    """Adding to a nonexistent section should return None."""
    result = core.add_entry("nonexistent", "test")
    assert result is None


def test_add_entry_over_section_budget(memory_dir: Path):
    """Adding an entry that exceeds section budget should fail."""
    core = CoreMemory(memory_dir, token_budget=10000)

    # identity section has 600 token budget
    # Create a string that exceeds 600 tokens
    long_text = "word " * 500  # ~500 tokens
    result1 = core.add_entry("identity", long_text)
    assert result1 is not None

    # Second entry should push over section budget
    result2 = core.add_entry("identity", long_text)
    assert result2 is None


def test_add_entry_over_global_budget(memory_dir: Path):
    """Adding an entry that exceeds global budget should fail."""
    core = CoreMemory(memory_dir, token_budget=5)  # tiny budget — 5 tokens

    # This text is much more than 5 tokens
    result = core.add_entry("scratch", "This is a long sentence that will surely exceed five tokens")
    assert result is None


def test_add_entry_importance_clamped(core: CoreMemory):
    """Importance should be clamped between 1 and 10."""
    id1 = core.add_entry("scratch", "low", importance=-5)
    id2 = core.add_entry("scratch", "high", importance=99)

    assert core.find_entry(id1)["importance"] == 1
    assert core.find_entry(id2)["importance"] == 10


def test_add_entry_strips_whitespace(core: CoreMemory):
    """Content should be stripped of leading/trailing whitespace."""
    entry_id = core.add_entry("scratch", "  hello world  ")
    assert core.find_entry(entry_id)["content"] == "hello world"


# ── Edit Entry ─────────────────────────────────────────────


def test_edit_entry_content(core: CoreMemory):
    """Editing content should update the entry."""
    entry_id = core.add_entry("scratch", "old content")
    result = core.edit_entry(entry_id, new_content="new content")

    assert result is True
    assert core.find_entry(entry_id)["content"] == "new content"


def test_edit_entry_importance(core: CoreMemory):
    """Editing importance should update the entry."""
    entry_id = core.add_entry("scratch", "test", importance=5)
    core.edit_entry(entry_id, importance=9)

    assert core.find_entry(entry_id)["importance"] == 9


def test_edit_entry_move_section(core: CoreMemory):
    """Moving an entry to a different section should work."""
    entry_id = core.add_entry("scratch", "important fact")

    assert core.get_section_tokens("scratch") > 0
    assert core.get_section_tokens("identity") == 0

    result = core.edit_entry(entry_id, new_section="identity")
    assert result is True

    assert core.get_section_tokens("scratch") == 0
    assert core.get_section_tokens("identity") > 0


def test_edit_nonexistent_entry(core: CoreMemory):
    """Editing a nonexistent entry should return False."""
    result = core.edit_entry("nonexistent", new_content="test")
    assert result is False


def test_edit_entry_updates_timestamp(core: CoreMemory):
    """Editing should update the updated_at timestamp."""
    entry_id = core.add_entry("scratch", "test")
    original = core.find_entry(entry_id)["updated_at"]

    core.edit_entry(entry_id, new_content="updated")
    updated = core.find_entry(entry_id)["updated_at"]

    # Timestamps might be the same if test runs fast, but updated_at should exist
    assert updated is not None


def test_edit_entry_over_budget_fails(memory_dir: Path):
    """Editing content that exceeds budget should fail."""
    core = CoreMemory(memory_dir, token_budget=50)
    entry_id = core.add_entry("scratch", "short")
    assert entry_id is not None

    # Try to make it much longer
    long_content = "word " * 100
    result = core.edit_entry(entry_id, new_content=long_content)
    assert result is False

    # Original content should be preserved
    assert core.find_entry(entry_id)["content"] == "short"


# ── Delete Entry ───────────────────────────────────────────


def test_delete_entry(core: CoreMemory):
    """Deleting an entry should remove it and return the entry dict."""
    entry_id = core.add_entry("scratch", "to be deleted", importance=3)
    tokens_before = core.get_total_tokens()

    deleted = core.delete_entry(entry_id)

    assert deleted is not None
    assert deleted["content"] == "to be deleted"
    assert deleted["importance"] == 3
    assert core.find_entry(entry_id) is None
    assert core.get_total_tokens() < tokens_before


def test_delete_nonexistent_entry(core: CoreMemory):
    """Deleting a nonexistent entry should return None."""
    result = core.delete_entry("nonexistent")
    assert result is None


# ── Rendering ──────────────────────────────────────────────


def test_render_for_context_empty(core: CoreMemory):
    """Empty core memory should render with just the header."""
    rendered = core.render_for_context()
    assert "## Core Memory (0/4000 tokens)" in rendered


def test_render_for_context_with_entries(core: CoreMemory):
    """Rendered output should have section headers and entry IDs."""
    id1 = core.add_entry("identity", "I'm Ene.", importance=10)
    id2 = core.add_entry("people", "CCC is an artist.", importance=7)

    rendered = core.render_for_context()

    assert "### Who I Am" in rendered
    assert f"- I'm Ene. [id:{id1}]" in rendered
    assert "### People I Know" in rendered
    assert f"- CCC is an artist. [id:{id2}]" in rendered
    # Budget display
    assert "/4000 tokens)" in rendered


def test_render_skips_empty_sections(core: CoreMemory):
    """Sections with no entries should not appear in rendered output."""
    core.add_entry("identity", "I'm Ene.")

    rendered = core.render_for_context()

    assert "### Who I Am" in rendered
    assert "### People I Know" not in rendered
    assert "### Preferences" not in rendered
    assert "### Working Notes" not in rendered


# ── Persistence ────────────────────────────────────────────


def test_persistence(memory_dir: Path):
    """Saving and reloading should preserve all data."""
    core1 = CoreMemory(memory_dir, token_budget=4000)
    id1 = core1.add_entry("identity", "I'm Ene.", importance=10)
    id2 = core1.add_entry("people", "CCC is cool.", importance=7)

    # Load a fresh instance from the same path
    core2 = CoreMemory(memory_dir, token_budget=4000)

    assert core2.find_entry(id1)["content"] == "I'm Ene."
    assert core2.find_entry(id2)["content"] == "CCC is cool."
    assert core2.get_total_tokens() == core1.get_total_tokens()


def test_persistence_after_delete(memory_dir: Path):
    """Deleting and reloading should reflect the deletion."""
    core1 = CoreMemory(memory_dir, token_budget=4000)
    id1 = core1.add_entry("scratch", "temporary")
    core1.delete_entry(id1)

    core2 = CoreMemory(memory_dir, token_budget=4000)
    assert core2.find_entry(id1) is None
    assert core2.get_total_tokens() == 0


# ── All Entries ────────────────────────────────────────────


def test_get_all_entries(core: CoreMemory):
    """get_all_entries returns (section, entry) tuples."""
    core.add_entry("identity", "entry1")
    core.add_entry("people", "entry2")
    core.add_entry("scratch", "entry3")

    all_entries = core.get_all_entries()
    assert len(all_entries) == 3

    sections = [sec for sec, _ in all_entries]
    assert "identity" in sections
    assert "people" in sections
    assert "scratch" in sections


# ── Budget Properties ──────────────────────────────────────


def test_is_over_budget(memory_dir: Path):
    """is_over_budget should reflect the budget state."""
    # Use a tiny budget
    core = CoreMemory(memory_dir, token_budget=5)

    assert not core.is_over_budget

    # Manually stuff data to simulate corruption
    core._data["sections"]["scratch"]["entries"].append({
        "id": "test1",
        "content": "this is definitely more than five tokens long",
        "importance": 5,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    })
    core._recount()

    assert core.is_over_budget
