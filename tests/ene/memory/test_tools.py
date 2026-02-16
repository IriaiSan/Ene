"""Tests for memory tools — save, edit, delete, search."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

import chromadb

from nanobot.ene.memory.core_memory import CoreMemory
from nanobot.ene.memory.vector_memory import VectorMemory
from nanobot.ene.memory.system import MemorySystem
from nanobot.ene.memory.tools import (
    SaveMemoryTool,
    EditMemoryTool,
    DeleteMemoryTool,
    SearchMemoryTool,
)


# ── Fixtures ───────────────────────────────────────────────


@pytest.fixture
def memory_dir(tmp_path: Path) -> Path:
    """Create a temporary memory directory."""
    d = tmp_path / "memory"
    d.mkdir()
    (d / "diary").mkdir()
    return d


@pytest.fixture
def chroma_client():
    """Fresh in-memory ChromaDB client."""
    client = chromadb.Client()
    for col in client.list_collections():
        client.delete_collection(col.name)
    return client


@pytest.fixture
def system(memory_dir: Path, chroma_client) -> MemorySystem:
    """Create a MemorySystem with in-memory ChromaDB."""
    sys = MemorySystem(
        workspace=memory_dir.parent,
        token_budget=4000,
    )
    # Initialize manually with our test client
    sys._core = CoreMemory(memory_dir, token_budget=4000)
    sys._vector = VectorMemory(client=chroma_client)
    return sys


@pytest.fixture
def save_tool(system: MemorySystem) -> SaveMemoryTool:
    return SaveMemoryTool(system)


@pytest.fixture
def edit_tool(system: MemorySystem) -> EditMemoryTool:
    return EditMemoryTool(system)


@pytest.fixture
def delete_tool(system: MemorySystem) -> DeleteMemoryTool:
    return DeleteMemoryTool(system)


@pytest.fixture
def search_tool(system: MemorySystem) -> SearchMemoryTool:
    return SearchMemoryTool(system)


# ── SaveMemoryTool ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_memory_creates_entry(save_tool: SaveMemoryTool, system: MemorySystem):
    """save_memory should add an entry to core memory."""
    result = await save_tool.execute(
        memory="I'm Ene, Dad built me.",
        section="identity",
        importance=10,
    )

    assert "Saved to identity" in result
    assert "[id:" in result
    assert system.core.get_total_tokens() > 0


@pytest.mark.asyncio
async def test_save_memory_default_importance(save_tool: SaveMemoryTool, system: MemorySystem):
    """save_memory without importance should default to 5."""
    result = await save_tool.execute(
        memory="test note",
        section="scratch",
    )

    assert "Saved to scratch" in result
    entries = system.core.get_all_entries()
    assert entries[0][1]["importance"] == 5


@pytest.mark.asyncio
async def test_save_memory_empty_content(save_tool: SaveMemoryTool):
    """save_memory with empty content should return error."""
    result = await save_tool.execute(memory="", section="scratch")
    assert "Error" in result


@pytest.mark.asyncio
async def test_save_memory_over_budget(memory_dir: Path, chroma_client):
    """save_memory over budget should return error with budget info."""
    sys = MemorySystem(workspace=memory_dir.parent, token_budget=5)
    sys._core = CoreMemory(memory_dir, token_budget=5)
    sys._vector = VectorMemory(client=chroma_client)
    tool = SaveMemoryTool(sys)

    result = await tool.execute(
        memory="This sentence has way more than five tokens in it so it should fail",
        section="scratch",
    )

    assert "Error" in result
    assert "budget" in result.lower()


@pytest.mark.asyncio
async def test_save_memory_shows_budget(save_tool: SaveMemoryTool):
    """save_memory result should show current budget usage."""
    result = await save_tool.execute(
        memory="test entry",
        section="scratch",
    )

    assert "/4000 tokens" in result


# ── EditMemoryTool ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_memory_updates_content(
    save_tool: SaveMemoryTool,
    edit_tool: EditMemoryTool,
    system: MemorySystem,
):
    """edit_memory should update entry content."""
    save_result = await save_tool.execute(
        memory="old content",
        section="scratch",
    )
    # Extract entry_id from result
    entry_id = save_result.split("[id:")[1].split("]")[0]

    result = await edit_tool.execute(
        entry_id=entry_id,
        new_content="new content",
    )

    assert "Updated" in result
    assert entry_id in result
    assert system.core.find_entry(entry_id)["content"] == "new content"


@pytest.mark.asyncio
async def test_edit_memory_bad_id(edit_tool: EditMemoryTool):
    """edit_memory with nonexistent ID should return error."""
    result = await edit_tool.execute(
        entry_id="nonexistent",
        new_content="test",
    )

    assert "Error" in result
    assert "nonexistent" in result


@pytest.mark.asyncio
async def test_edit_memory_empty_id(edit_tool: EditMemoryTool):
    """edit_memory with empty ID should return error."""
    result = await edit_tool.execute(entry_id="")
    assert "Error" in result


@pytest.mark.asyncio
async def test_edit_memory_move_section(
    save_tool: SaveMemoryTool,
    edit_tool: EditMemoryTool,
    system: MemorySystem,
):
    """edit_memory should move entry to new section."""
    save_result = await save_tool.execute(
        memory="to be moved",
        section="scratch",
    )
    entry_id = save_result.split("[id:")[1].split("]")[0]

    result = await edit_tool.execute(
        entry_id=entry_id,
        new_section="identity",
    )

    assert "Updated" in result
    assert system.core.get_section_tokens("scratch") == 0
    assert system.core.get_section_tokens("identity") > 0


# ── DeleteMemoryTool ───────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_memory_archives(
    save_tool: SaveMemoryTool,
    delete_tool: DeleteMemoryTool,
    system: MemorySystem,
):
    """delete_memory with archive=True should archive to vector store."""
    save_result = await save_tool.execute(
        memory="to be archived",
        section="scratch",
        importance=7,
    )
    entry_id = save_result.split("[id:")[1].split("]")[0]

    result = await delete_tool.execute(entry_id=entry_id, archive=True)

    assert "archived" in result.lower()
    assert system.core.find_entry(entry_id) is None
    assert system.vector.get_memory_count() == 1


@pytest.mark.asyncio
async def test_delete_memory_permanent(
    save_tool: SaveMemoryTool,
    delete_tool: DeleteMemoryTool,
    system: MemorySystem,
):
    """delete_memory with archive=False should permanently delete."""
    save_result = await save_tool.execute(
        memory="to be destroyed",
        section="scratch",
    )
    entry_id = save_result.split("[id:")[1].split("]")[0]

    result = await delete_tool.execute(entry_id=entry_id, archive=False)

    assert "Permanently deleted" in result
    assert system.core.find_entry(entry_id) is None
    assert system.vector.get_memory_count() == 0


@pytest.mark.asyncio
async def test_delete_memory_bad_id(delete_tool: DeleteMemoryTool):
    """delete_memory with nonexistent ID should return error."""
    result = await delete_tool.execute(entry_id="nonexistent")
    assert "Error" in result


@pytest.mark.asyncio
async def test_delete_memory_empty_id(delete_tool: DeleteMemoryTool):
    """delete_memory with empty ID should return error."""
    result = await delete_tool.execute(entry_id="")
    assert "Error" in result


@pytest.mark.asyncio
async def test_delete_memory_default_archives(
    save_tool: SaveMemoryTool,
    delete_tool: DeleteMemoryTool,
    system: MemorySystem,
):
    """delete_memory without archive param should default to True."""
    save_result = await save_tool.execute(
        memory="default archive test",
        section="scratch",
    )
    entry_id = save_result.split("[id:")[1].split("]")[0]

    result = await delete_tool.execute(entry_id=entry_id)

    assert "archived" in result.lower()
    assert system.vector.get_memory_count() == 1


# ── SearchMemoryTool ───────────────────────────────────────


@pytest.mark.asyncio
async def test_search_memory_returns_results(
    search_tool: SearchMemoryTool,
    system: MemorySystem,
):
    """search_memory should return formatted results."""
    # Add some memories to vector store
    system.vector.add_memory("Ene loves coding.", memory_type="fact", importance=8)
    system.vector.add_memory("Dad likes ramen.", memory_type="fact", importance=6)

    result = await search_tool.execute(query="What does Ene love?")

    assert "Found" in result
    assert "memories" in result.lower()


@pytest.mark.asyncio
async def test_search_memory_empty_results(
    search_tool: SearchMemoryTool,
):
    """search_memory with no matches should return helpful message."""
    result = await search_tool.execute(query="something completely random and unique")

    # Empty store, so no results
    assert "No memories found" in result


@pytest.mark.asyncio
async def test_search_memory_empty_query(search_tool: SearchMemoryTool):
    """search_memory with empty query should return error."""
    result = await search_tool.execute(query="")
    assert "Error" in result


@pytest.mark.asyncio
async def test_search_memory_type_filter(
    search_tool: SearchMemoryTool,
    system: MemorySystem,
):
    """search_memory with type filter should only return matching types."""
    system.vector.add_memory("diary entry about coding", memory_type="diary")
    system.vector.add_memory("fact about Ene", memory_type="fact")

    result = await search_tool.execute(
        query="coding",
        memory_type="diary",
    )

    assert "Found" in result


@pytest.mark.asyncio
async def test_search_memory_no_vector_store(memory_dir: Path):
    """search_memory without vector store should return error."""
    sys = MemorySystem(workspace=memory_dir.parent, token_budget=4000)
    sys._core = CoreMemory(memory_dir, token_budget=4000)
    sys._vector = None
    tool = SearchMemoryTool(sys)

    result = await tool.execute(query="test")
    assert "Error" in result
    assert "not available" in result.lower()


# ── Tool Schema ────────────────────────────────────────────


def test_tool_schemas(system: MemorySystem):
    """All tools should produce valid OpenAI function schemas."""
    tools = [
        SaveMemoryTool(system),
        EditMemoryTool(system),
        DeleteMemoryTool(system),
        SearchMemoryTool(system),
    ]

    for tool in tools:
        schema = tool.to_schema()
        assert schema["type"] == "function"
        assert "name" in schema["function"]
        assert "description" in schema["function"]
        assert "parameters" in schema["function"]
        assert schema["function"]["parameters"]["type"] == "object"
        assert "required" in schema["function"]["parameters"]


def test_tool_names(system: MemorySystem):
    """Tool names should match expected values."""
    assert SaveMemoryTool(system).name == "save_memory"
    assert EditMemoryTool(system).name == "edit_memory"
    assert DeleteMemoryTool(system).name == "delete_memory"
    assert SearchMemoryTool(system).name == "search_memory"
