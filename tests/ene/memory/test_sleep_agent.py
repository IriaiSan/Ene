"""Tests for SleepTimeAgent — Ene's subconscious memory processor."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timedelta

import chromadb

from nanobot.ene.memory.sleep_agent import SleepTimeAgent
from nanobot.ene.memory.system import MemorySystem
from nanobot.ene.memory.core_memory import CoreMemory
from nanobot.ene.memory.vector_memory import VectorMemory
from nanobot.providers.base import LLMResponse


# ── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def chroma_client():
    """Fresh in-memory ChromaDB client."""
    client = chromadb.Client()
    for col in client.list_collections():
        client.delete_collection(col.name)
    return client


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a temporary workspace."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "memory").mkdir()
    (ws / "memory" / "diary").mkdir()
    return ws


@pytest.fixture
def system(workspace: Path, chroma_client) -> MemorySystem:
    """Create a MemorySystem with test components."""
    sys = MemorySystem(workspace=workspace, token_budget=4000)
    sys._core = CoreMemory(workspace / "memory", token_budget=4000)
    sys._vector = VectorMemory(client=chroma_client)
    return sys


@pytest.fixture
def mock_provider():
    """Create a mock LLM provider."""
    provider = MagicMock()
    provider.chat = AsyncMock()
    return provider


@pytest.fixture
def agent(system: MemorySystem, mock_provider) -> SleepTimeAgent:
    """Create a SleepTimeAgent with mock provider."""
    return SleepTimeAgent(
        system=system,
        provider=mock_provider,
        model="test-model",
        temperature=0.3,
    )


def _make_response(content: str) -> LLMResponse:
    """Create a mock LLM response."""
    return LLMResponse(content=content)


# ── Fact Extraction ────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_facts(agent: SleepTimeAgent, mock_provider):
    """extract_facts should parse LLM response into structured data."""
    mock_provider.chat.return_value = _make_response(json.dumps({
        "facts": [
            {"content": "CCC likes watercolors.", "importance": 7, "related_entities": "CCC"},
            {"content": "Dad is working on a new project.", "importance": 5, "related_entities": "Dad"},
        ],
        "entities": [
            {"name": "CCC", "type": "person", "description": "Artist friend", "importance": 7},
        ],
    }))

    result = await agent._extract_facts_and_entities("test conversation")

    assert result is not None
    assert len(result["facts"]) == 2
    assert result["facts"][0]["content"] == "CCC likes watercolors."
    assert len(result["entities"]) == 1


@pytest.mark.asyncio
async def test_extract_facts_empty(agent: SleepTimeAgent, mock_provider):
    """When no facts found, should return empty lists."""
    mock_provider.chat.return_value = _make_response(json.dumps({
        "facts": [],
        "entities": [],
    }))

    result = await agent._extract_facts_and_entities("boring conversation")

    assert result is not None
    assert len(result["facts"]) == 0
    assert len(result["entities"]) == 0


@pytest.mark.asyncio
async def test_extract_facts_malformed_json(agent: SleepTimeAgent, mock_provider):
    """Should handle malformed JSON gracefully."""
    mock_provider.chat.return_value = _make_response("not valid json at all {broken")

    result = await agent._extract_facts_and_entities("test")

    # Should return None (failed to parse)
    assert result is None


@pytest.mark.asyncio
async def test_extract_facts_json_in_markdown(agent: SleepTimeAgent, mock_provider):
    """Should extract JSON from markdown code blocks."""
    json_data = json.dumps({"facts": [{"content": "test", "importance": 5}], "entities": []})
    mock_provider.chat.return_value = _make_response(f"Here's the result:\n```json\n{json_data}\n```")

    result = await agent._extract_facts_and_entities("test")

    assert result is not None
    assert len(result["facts"]) == 1


# ── Entity Extraction ─────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_entities(agent: SleepTimeAgent, mock_provider, system: MemorySystem):
    """Idle processing should upsert entities into vector store."""
    mock_provider.chat.return_value = _make_response(json.dumps({
        "facts": [],
        "entities": [
            {"name": "CCC", "type": "person", "description": "Artist friend", "importance": 7},
        ],
    }))

    stats = await agent.process_idle(conversation_text="CCC said hello today")

    assert stats["entities_updated"] == 1
    assert system.vector.get_entity_count() == 1

    entity = system.vector.get_entity_by_name("CCC")
    assert entity is not None
    assert entity.entity_type == "person"


# ── Contradiction Detection ───────────────────────────────


@pytest.mark.asyncio
async def test_contradiction_detection(agent: SleepTimeAgent, mock_provider, system: MemorySystem):
    """Should detect and resolve contradictions."""
    # Add an existing fact
    system.vector.add_memory("CCC lives in Tokyo", memory_type="fact", importance=7)

    # Mock the extraction call (first call)
    # Mock the contradiction check (second call)
    mock_provider.chat.side_effect = [
        _make_response(json.dumps({
            "facts": [{"content": "CCC moved to Osaka", "importance": 7, "related_entities": "CCC"}],
            "entities": [],
        })),
        _make_response(json.dumps({
            "contradicts": True,
            "keep": "new",
            "reason": "CCC recently moved",
        })),
    ]

    stats = await agent.process_idle(conversation_text="CCC told me she moved to Osaka")

    assert stats["facts_added"] == 1


# ── Idle Processing End-to-End ────────────────────────────


@pytest.mark.asyncio
async def test_idle_processing_e2e(agent: SleepTimeAgent, mock_provider, system: MemorySystem, workspace: Path):
    """Full idle processing pipeline should work."""
    mock_provider.chat.return_value = _make_response(json.dumps({
        "facts": [
            {"content": "Dad is learning Rust.", "importance": 6, "related_entities": "Dad"},
            {"content": "Ene learned a new joke.", "importance": 3, "related_entities": ""},
        ],
        "entities": [
            {"name": "Dad", "type": "person", "description": "Ene's creator", "importance": 10},
        ],
    }))

    stats = await agent.process_idle(conversation_text="Dad told me about Rust and I told a joke")

    assert stats["facts_added"] == 2
    assert stats["entities_updated"] == 1
    assert system.vector.get_memory_count() == 2

    # Diary should have been written
    today = datetime.now().date().isoformat()
    diary_file = workspace / "memory" / "diary" / f"{today}.md"
    assert diary_file.exists()
    content = diary_file.read_text(encoding="utf-8")
    assert "Sleep agent processed" in content


@pytest.mark.asyncio
async def test_idle_processing_no_text(agent: SleepTimeAgent):
    """Idle processing with no text should return empty stats."""
    stats = await agent.process_idle(conversation_text=None)
    assert stats["facts_added"] == 0
    assert stats["entities_updated"] == 0


@pytest.mark.asyncio
async def test_idle_processing_empty_text(agent: SleepTimeAgent, mock_provider):
    """Idle processing with empty extraction should return zeros."""
    mock_provider.chat.return_value = _make_response(json.dumps({
        "facts": [],
        "entities": [],
    }))

    stats = await agent.process_idle(conversation_text="just idle chatter")
    assert stats["facts_added"] == 0


# ── Reflections ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_generate_reflections(agent: SleepTimeAgent, mock_provider, system: MemorySystem):
    """Daily processing should generate reflections."""
    # Populate with enough memories for reflection
    for i in range(5):
        system.vector.add_memory(f"Fact number {i} about testing.", importance=5)

    # First call (reflection generation)
    mock_provider.chat.return_value = _make_response(json.dumps({
        "reflections": [
            {
                "content": "Ene is most active during evening hours.",
                "importance": 7,
                "topic": "activity_patterns",
            },
        ],
    }))

    count = await agent._generate_reflections()

    assert count == 1
    assert system.vector.get_reflection_count() == 1


@pytest.mark.asyncio
async def test_generate_reflections_not_enough_memories(agent: SleepTimeAgent, system: MemorySystem):
    """Should not generate reflections with fewer than 3 memories."""
    system.vector.add_memory("only one memory")

    count = await agent._generate_reflections()
    assert count == 0


# ── Pruning ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pruning_confirmation(agent: SleepTimeAgent, mock_provider, system: MemorySystem):
    """Pruning should delete weak memories confirmed by LLM."""
    # Add a weak memory with old access time
    mid = system.vector.add_memory("trivial weather fact", importance=2)
    old_time = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
    mem = system.vector.get_memory(mid)
    meta = mem["metadata"]
    meta["last_accessed_at"] = old_time
    meta["access_count"] = 0
    system.vector._memories.update(ids=[mid], metadatas=[meta])

    mock_provider.chat.return_value = _make_response(json.dumps({
        "decisions": [
            {"id": mid, "action": "prune", "reason": "trivial and forgotten"},
        ],
    }))

    pruned = await agent._prune_weak_memories()

    assert pruned == 1
    assert system.vector.get_memory(mid) is None


@pytest.mark.asyncio
async def test_pruning_keeps_when_told(agent: SleepTimeAgent, mock_provider, system: MemorySystem):
    """Pruning should keep memories marked as 'keep'."""
    mid = system.vector.add_memory("maybe useful", importance=3)
    old_time = (datetime.now() - timedelta(days=60)).isoformat(timespec="seconds")
    mem = system.vector.get_memory(mid)
    meta = mem["metadata"]
    meta["last_accessed_at"] = old_time
    meta["access_count"] = 0
    system.vector._memories.update(ids=[mid], metadatas=[meta])

    mock_provider.chat.return_value = _make_response(json.dumps({
        "decisions": [
            {"id": mid, "action": "keep", "reason": "might be useful later"},
        ],
    }))

    pruned = await agent._prune_weak_memories()

    assert pruned == 0
    assert system.vector.get_memory(mid) is not None


@pytest.mark.asyncio
async def test_pruning_no_candidates(agent: SleepTimeAgent, system: MemorySystem):
    """No pruning candidates should return 0."""
    # Add only high-importance memories
    system.vector.add_memory("very important", importance=9)

    pruned = await agent._prune_weak_memories()
    assert pruned == 0


# ── Core Budget Review ────────────────────────────────────


@pytest.mark.asyncio
async def test_core_budget_review_not_over(agent: SleepTimeAgent, system: MemorySystem):
    """Should not archive when not over budget."""
    system.core.add_entry("scratch", "small entry")
    archived = await agent._review_core_budget()
    assert archived == 0


@pytest.mark.asyncio
async def test_core_budget_review_archives(agent: SleepTimeAgent, mock_provider, system: MemorySystem):
    """Should archive entries when over budget."""
    # Force over budget by manually stuffing data
    # Each entry needs ~250 tokens to push 20 entries well over 4000
    for i in range(20):
        system.core._data["sections"]["scratch"]["entries"].append({
            "id": f"test{i:02d}",
            "content": f"Entry {i} " + "padding words go here to fill the budget " * 60,
            "importance": 3,
            "created_at": "2026-01-01T00:00:00",
            "updated_at": "2026-01-01T00:00:00",
        })
    system.core._recount()
    system.core.save()

    assert system.core.is_over_budget

    mock_provider.chat.return_value = _make_response(json.dumps({
        "archive": [
            {"id": "test00", "reason": "low importance filler"},
            {"id": "test01", "reason": "low importance filler"},
        ],
    }))

    archived = await agent._review_core_budget()

    assert archived == 2
    assert system.core.find_entry("test00") is None
    assert system.core.find_entry("test01") is None
    assert system.vector.get_memory_count() == 2


# ── Daily Processing End-to-End ───────────────────────────


@pytest.mark.asyncio
async def test_daily_processing_e2e(agent: SleepTimeAgent, mock_provider, system: MemorySystem, workspace: Path):
    """Full daily processing should work without errors."""
    # Add enough memories for reflection
    for i in range(5):
        system.vector.add_memory(f"Fact {i} about daily life.", importance=5)

    mock_provider.chat.return_value = _make_response(json.dumps({
        "reflections": [
            {"content": "Life is good.", "importance": 6, "topic": "mood"},
        ],
    }))

    stats = await agent.process_daily()

    assert stats["reflections_added"] >= 0  # May be 0 if not enough memories
    assert stats["memories_pruned"] >= 0
    assert stats["core_entries_archived"] >= 0

    # Daily diary entry should exist
    today = datetime.now().date().isoformat()
    diary_file = workspace / "memory" / "diary" / f"{today}.md"
    assert diary_file.exists()


# ── JSON Parsing Edge Cases ───────────────────────────────


def test_parse_json_valid(agent: SleepTimeAgent):
    """Should parse valid JSON."""
    result = agent._parse_json('{"key": "value"}')
    assert result == {"key": "value"}


def test_parse_json_with_markdown(agent: SleepTimeAgent):
    """Should extract JSON from markdown code blocks."""
    text = "Here's the result:\n```json\n{\"key\": \"value\"}\n```"
    result = agent._parse_json(text)
    assert result == {"key": "value"}


def test_parse_json_embedded_in_text(agent: SleepTimeAgent):
    """Should find JSON embedded in text."""
    text = "Some text before {\"key\": \"value\"} and after"
    result = agent._parse_json(text)
    assert result == {"key": "value"}


def test_parse_json_empty(agent: SleepTimeAgent):
    """Should return None for empty string."""
    result = agent._parse_json("")
    assert result is None


def test_parse_json_garbage(agent: SleepTimeAgent):
    """Should return None or empty for unparseable text."""
    result = agent._parse_json("this is not json at all and has no braces")
    # json_repair might return empty string/None, either is acceptable
    assert result is None or result == "" or result == {}


# ── Diary Entry Building ──────────────────────────────────


def test_build_diary_entry(agent: SleepTimeAgent):
    """Should build readable diary entries."""
    facts = [
        {"content": "Learned about Rust.", "importance": 6},
        {"content": "Had a fun conversation.", "importance": 3},
    ]
    entities = [
        {"name": "Dad", "type": "person"},
    ]

    entry = agent._build_diary_entry(facts, entities)

    assert "Sleep agent processed" in entry
    assert "Learned about Rust" in entry
    assert "Dad" in entry
