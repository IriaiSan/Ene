"""Tests for social tools — update_person_note, view_person, list_people."""

import pytest
from pathlib import Path

from nanobot.ene.social.person import PersonRegistry
from nanobot.ene.social.graph import SocialGraph
from nanobot.ene.social.tools import (
    UpdatePersonNoteTool,
    ViewPersonTool,
    ListPeopleTool,
)


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def social_dir(tmp_path: Path) -> Path:
    d = tmp_path / "social"
    d.mkdir()
    return d


@pytest.fixture
def registry(social_dir: Path) -> PersonRegistry:
    return PersonRegistry(social_dir)


@pytest.fixture
def graph(registry: PersonRegistry) -> SocialGraph:
    return SocialGraph(registry)


@pytest.fixture
def note_tool(registry: PersonRegistry) -> UpdatePersonNoteTool:
    return UpdatePersonNoteTool(registry)


@pytest.fixture
def view_tool(registry: PersonRegistry, graph: SocialGraph) -> ViewPersonTool:
    return ViewPersonTool(registry, graph)


@pytest.fixture
def list_tool(registry: PersonRegistry) -> ListPeopleTool:
    return ListPeopleTool(registry)


# ── UpdatePersonNoteTool Tests ────────────────────────────


class TestUpdatePersonNoteTool:
    @pytest.mark.asyncio
    async def test_add_note(self, note_tool, registry):
        registry.create("discord:100", "Alice")
        result = await note_tool.execute(person_name="Alice", note="likes cats")
        assert "Noted" in result
        assert "Alice" in result

    @pytest.mark.asyncio
    async def test_add_note_case_insensitive(self, note_tool, registry):
        registry.create("discord:101", "Bob")
        result = await note_tool.execute(person_name="bob", note="test")
        assert "Noted" in result

    @pytest.mark.asyncio
    async def test_unknown_person(self, note_tool):
        result = await note_tool.execute(person_name="Nobody", note="test")
        assert "don't know" in result

    @pytest.mark.asyncio
    async def test_empty_name(self, note_tool):
        result = await note_tool.execute(person_name="", note="test")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_empty_note(self, note_tool, registry):
        registry.create("discord:102", "Charlie")
        result = await note_tool.execute(person_name="Charlie", note="")
        assert "Error" in result

    @pytest.mark.asyncio
    async def test_tool_metadata(self, note_tool):
        assert note_tool.name == "update_person_note"
        assert "note" in note_tool.description.lower()
        assert "person_name" in note_tool.parameters["properties"]


# ── ViewPersonTool Tests ──────────────────────────────────


class TestViewPersonTool:
    @pytest.mark.asyncio
    async def test_view_person(self, view_tool, registry):
        profile = registry.create("discord:200", "Alice")
        profile.summary = "An artist who likes watercolors."
        registry.update(profile)

        result = await view_tool.execute(person_name="Alice")
        assert "Alice" in result
        assert "stranger" in result  # Default tier
        assert "watercolors" in result

    @pytest.mark.asyncio
    async def test_view_person_with_notes(self, view_tool, registry):
        profile = registry.create("discord:201", "Bob")
        registry.add_note(profile.id, "loves pizza")
        registry.add_note(profile.id, "has a cat named Mochi")

        result = await view_tool.execute(person_name="Bob")
        assert "pizza" in result
        assert "Mochi" in result

    @pytest.mark.asyncio
    async def test_view_person_with_connections(self, view_tool, registry, graph):
        alice = registry.create("discord:202", "Alice2")
        registry.add_connection("p_dad001", alice.id, "friend", "IRL")

        result = await view_tool.execute(person_name="Alice2")
        assert "Dad" in result

    @pytest.mark.asyncio
    async def test_view_unknown_person(self, view_tool):
        result = await view_tool.execute(person_name="Nobody")
        assert "don't know" in result

    @pytest.mark.asyncio
    async def test_tool_metadata(self, view_tool):
        assert view_tool.name == "view_person"
        assert "person_name" in view_tool.parameters["properties"]


# ── ListPeopleTool Tests ─────────────────────────────────


class TestListPeopleTool:
    @pytest.mark.asyncio
    async def test_list_people(self, list_tool, registry):
        registry.create("discord:300", "Alice")
        registry.create("discord:301", "Bob")

        result = await list_tool.execute()
        assert "Alice" in result
        assert "Bob" in result
        assert "Dad" in result

    @pytest.mark.asyncio
    async def test_list_sorted_by_trust(self, list_tool, registry):
        # Dad is always first (score=1.0)
        result = await list_tool.execute()
        lines = result.strip().split("\n")
        # First person entry should be Dad
        person_lines = [l for l in lines if l.startswith("- **")]
        assert "Dad" in person_lines[0]

    @pytest.mark.asyncio
    async def test_list_shows_count(self, list_tool, registry):
        registry.create("discord:302", "Charlie")
        result = await list_tool.execute()
        assert "People I know" in result

    @pytest.mark.asyncio
    async def test_tool_metadata(self, list_tool):
        assert list_tool.name == "list_people"
        assert "list" in list_tool.description.lower()
