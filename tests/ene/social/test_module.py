"""Integration tests for SocialModule."""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock

from nanobot.ene import EneContext, ModuleRegistry
from nanobot.ene.social import SocialModule
from nanobot.ene.social.person import DAD_IDS
from nanobot.ene.social.trust import TrustCalculator


# ── Fixtures ──────────────────────────────────────────────


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a workspace directory with memory/social structure."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "memory").mkdir()
    return ws


@pytest.fixture
def ctx(workspace: Path) -> EneContext:
    """Create a mock EneContext."""
    return EneContext(
        workspace=workspace,
        provider=MagicMock(),
        config=MagicMock(),
        bus=MagicMock(),
        sessions=MagicMock(),
    )


@pytest.fixture
async def module(ctx: EneContext) -> SocialModule:
    """Create and initialize a SocialModule."""
    m = SocialModule()
    await m.initialize(ctx)
    return m


@pytest.fixture
def make_msg():
    """Factory for creating mock InboundMessage objects."""
    def _make(
        channel: str = "discord",
        sender_id: str = "999999",
        content: str = "hello",
        guild_id: str | None = "guild123",
        author_name: str = "TestUser",
    ):
        msg = MagicMock()
        msg.channel = channel
        msg.sender_id = sender_id
        msg.content = content
        msg.metadata = {
            "guild_id": guild_id,
            "author_name": author_name,
        }
        msg.media = []
        return msg
    return _make


# ── Module Lifecycle Tests ────────────────────────────────


class TestModuleLifecycle:
    @pytest.mark.asyncio
    async def test_initialize(self, module):
        assert module.name == "social"
        assert module.registry is not None
        assert module.graph is not None

    @pytest.mark.asyncio
    async def test_dad_exists_after_init(self, module):
        for pid in DAD_IDS:
            person = module.registry.get_by_platform_id(pid)
            assert person is not None
            assert person.trust.score == 1.0
            assert person.trust.tier == "inner_circle"

    @pytest.mark.asyncio
    async def test_get_tools(self, module):
        tools = module.get_tools()
        names = {t.name for t in tools}
        assert "update_person_note" in names
        assert "view_person" in names
        assert "list_people" in names

    @pytest.mark.asyncio
    async def test_get_context_block(self, module):
        block = module.get_context_block()
        assert block is not None
        assert "Social Awareness" in block
        assert "stranger" in block
        assert "inner_circle" in block

    @pytest.mark.asyncio
    async def test_shutdown(self, module):
        await module.shutdown()  # Should not raise


# ── Person Card Tests ─────────────────────────────────────


class TestPersonCard:
    @pytest.mark.asyncio
    async def test_unknown_person_card(self, module):
        module.set_sender_context("discord:unknown123", {})
        card = module.get_context_block_for_message("hello")
        assert card is not None
        assert "Unknown" in card
        assert "stranger" in card
        assert "discord:unknown123" in card

    @pytest.mark.asyncio
    async def test_known_person_card(self, module, make_msg):
        # Create a person by sending a message
        msg = make_msg(sender_id="111222", author_name="Alice")
        await module.on_message(msg, responded=True)

        # Now get their card
        module.set_sender_context("discord:111222", msg.metadata)
        card = module.get_context_block_for_message("hello")
        assert card is not None
        assert "Alice" in card

    @pytest.mark.asyncio
    async def test_dad_person_card(self, module):
        dad_pid = list(DAD_IDS)[0]
        module.set_sender_context(dad_pid, {})
        card = module.get_context_block_for_message("hello")
        assert card is not None
        assert "Dad" in card
        assert "inner_circle" in card


# ── Interaction Recording Tests ───────────────────────────


class TestInteractionRecording:
    @pytest.mark.asyncio
    async def test_auto_create_profile(self, module, make_msg):
        msg = make_msg(sender_id="555666", author_name="NewPerson")
        await module.on_message(msg, responded=True)

        person = module.registry.get_by_platform_id("discord:555666")
        assert person is not None
        assert person.display_name == "NewPerson"

    @pytest.mark.asyncio
    async def test_interaction_updates_signals(self, module, make_msg):
        msg = make_msg(sender_id="777888", author_name="Counter")
        await module.on_message(msg, responded=True)
        await module.on_message(msg, responded=True)
        await module.on_message(msg, responded=True)

        person = module.registry.get_by_platform_id("discord:777888")
        assert person.trust.signals["message_count"] == 3
        assert person.trust.positive_interactions == 3

    @pytest.mark.asyncio
    async def test_trust_recalculated_on_message(self, module, make_msg):
        msg = make_msg(sender_id="999000", author_name="TrustMe")
        await module.on_message(msg, responded=True)

        person = module.registry.get_by_platform_id("discord:999000")
        # New user should have a calculated score (not zero)
        assert isinstance(person.trust.score, float)
        assert person.trust.tier == "stranger"  # Time gate blocks higher


# ── ModuleRegistry Integration ────────────────────────────


class TestModuleRegistryIntegration:
    @pytest.mark.asyncio
    async def test_sender_identity_bridge(self, ctx):
        registry = ModuleRegistry()
        module = SocialModule()
        registry.register(module)
        await registry.initialize_all(ctx)

        # Set sender
        registry.set_current_sender("12345", "discord", {"author_name": "Test"})
        assert registry.get_current_platform_id() == "discord:12345"

        # Dynamic context should trigger set_sender_context on modules
        dynamic = registry.get_all_dynamic_context("hello")
        # Should include the person card (unknown person)
        assert "Unknown" in dynamic or "stranger" in dynamic

    @pytest.mark.asyncio
    async def test_get_module(self, ctx):
        registry = ModuleRegistry()
        module = SocialModule()
        registry.register(module)
        await registry.initialize_all(ctx)

        found = registry.get_module("social")
        assert found is module
        assert found.registry is not None

    @pytest.mark.asyncio
    async def test_tools_aggregated(self, ctx):
        registry = ModuleRegistry()
        module = SocialModule()
        registry.register(module)
        await registry.initialize_all(ctx)

        all_tools = registry.get_all_tools()
        tool_names = {t.name for t in all_tools}
        assert "update_person_note" in tool_names
        assert "view_person" in tool_names
        assert "list_people" in tool_names


# ── Daily Maintenance Tests ───────────────────────────────


class TestDailyMaintenance:
    @pytest.mark.asyncio
    async def test_daily_records_history(self, module, make_msg):
        # Create a person
        msg = make_msg(sender_id="daily01", author_name="DailyTest")
        await module.on_message(msg, responded=True)

        # Run daily
        await module.on_daily()

        person = module.registry.get_by_platform_id("discord:daily01")
        assert len(person.trust.history) >= 1
        assert "date" in person.trust.history[-1]
        assert "score" in person.trust.history[-1]

    @pytest.mark.asyncio
    async def test_daily_doesnt_touch_dad(self, module):
        # Get Dad's score before daily
        dad = module.registry.get_by_platform_id(list(DAD_IDS)[0])
        score_before = dad.trust.score

        await module.on_daily()

        dad = module.registry.get_by_platform_id(list(DAD_IDS)[0])
        assert dad.trust.score == score_before
