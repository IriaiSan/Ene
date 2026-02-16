"""Tests for the Ene module infrastructure."""

import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock
from pathlib import Path

from nanobot.ene import EneModule, EneContext, ModuleRegistry


class StubModule(EneModule):
    """Minimal EneModule implementation for testing."""

    def __init__(self, module_name: str = "stub"):
        self._name = module_name
        self.initialized = False
        self.messages_received: list = []
        self.idle_calls: list[float] = []
        self.daily_called = False
        self.shutdown_called = False

    @property
    def name(self) -> str:
        return self._name

    async def initialize(self, ctx: EneContext) -> None:
        self.initialized = True

    def get_tools(self) -> list:
        return []

    def get_context_block(self) -> str | None:
        return f"[{self._name} context]"

    def get_context_block_for_message(self, message: str) -> str | None:
        if "trigger" in message:
            return f"[{self._name} dynamic for: {message}]"
        return None

    async def on_message(self, msg, responded: bool) -> None:
        self.messages_received.append((msg, responded))

    async def on_idle(self, idle_seconds: float) -> None:
        self.idle_calls.append(idle_seconds)

    async def on_daily(self) -> None:
        self.daily_called = True

    async def shutdown(self) -> None:
        self.shutdown_called = True


class StubToolModule(EneModule):
    """Module that provides mock tools."""

    @property
    def name(self) -> str:
        return "tool_provider"

    async def initialize(self, ctx: EneContext) -> None:
        pass

    def get_tools(self) -> list:
        tool1 = MagicMock()
        tool1.name = "tool_a"
        tool2 = MagicMock()
        tool2.name = "tool_b"
        return [tool1, tool2]


def test_register_module():
    registry = ModuleRegistry()
    module = StubModule("test")
    registry.register(module)

    assert "test" in registry.modules
    assert registry.get("test") is module


def test_register_duplicate_replaces():
    registry = ModuleRegistry()
    m1 = StubModule("test")
    m2 = StubModule("test")
    registry.register(m1)
    registry.register(m2)

    assert registry.get("test") is m2


def test_get_nonexistent_returns_none():
    registry = ModuleRegistry()
    assert registry.get("nonexistent") is None


@pytest.mark.asyncio
async def test_initialize_all():
    registry = ModuleRegistry()
    m1 = StubModule("a")
    m2 = StubModule("b")
    registry.register(m1)
    registry.register(m2)

    ctx = EneContext(
        workspace=Path("/tmp/test"),
        provider=MagicMock(),
        config=MagicMock(),
        bus=MagicMock(),
        sessions=MagicMock(),
    )
    await registry.initialize_all(ctx)

    assert m1.initialized
    assert m2.initialized


def test_get_all_tools():
    registry = ModuleRegistry()
    registry.register(StubModule("no_tools"))
    registry.register(StubToolModule())

    tools = registry.get_all_tools()
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"tool_a", "tool_b"}


def test_get_all_context_blocks():
    registry = ModuleRegistry()
    registry.register(StubModule("memory"))
    registry.register(StubModule("personality"))

    blocks = registry.get_all_context_blocks()
    assert "[memory context]" in blocks
    assert "[personality context]" in blocks


def test_get_all_dynamic_context_with_trigger():
    registry = ModuleRegistry()
    registry.register(StubModule("memory"))

    result = registry.get_all_dynamic_context("this has trigger in it")
    assert "[memory dynamic for:" in result


def test_get_all_dynamic_context_no_trigger():
    registry = ModuleRegistry()
    registry.register(StubModule("memory"))

    result = registry.get_all_dynamic_context("nothing special")
    assert result == ""


@pytest.mark.asyncio
async def test_notify_message():
    registry = ModuleRegistry()
    m1 = StubModule("a")
    m2 = StubModule("b")
    registry.register(m1)
    registry.register(m2)

    msg = MagicMock()
    await registry.notify_message(msg, responded=True)

    assert len(m1.messages_received) == 1
    assert m1.messages_received[0] == (msg, True)
    assert len(m2.messages_received) == 1


@pytest.mark.asyncio
async def test_notify_idle():
    registry = ModuleRegistry()
    m = StubModule("test")
    registry.register(m)

    await registry.notify_idle(300.0)

    assert m.idle_calls == [300.0]


@pytest.mark.asyncio
async def test_notify_daily():
    registry = ModuleRegistry()
    m = StubModule("test")
    registry.register(m)

    await registry.notify_daily()

    assert m.daily_called


@pytest.mark.asyncio
async def test_shutdown_all():
    registry = ModuleRegistry()
    m1 = StubModule("a")
    m2 = StubModule("b")
    registry.register(m1)
    registry.register(m2)

    await registry.shutdown_all()

    assert m1.shutdown_called
    assert m2.shutdown_called


@pytest.mark.asyncio
async def test_error_in_one_module_doesnt_break_others():
    """If one module raises in a hook, others should still be called."""
    registry = ModuleRegistry()

    class BrokenModule(StubModule):
        async def on_daily(self) -> None:
            raise RuntimeError("broken!")

    broken = BrokenModule("broken")
    healthy = StubModule("healthy")
    registry.register(broken)
    registry.register(healthy)

    # Should not raise
    await registry.notify_daily()

    # Healthy module should still have been called
    assert healthy.daily_called
