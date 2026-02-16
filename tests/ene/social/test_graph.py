"""Tests for social graph — connection queries."""

import pytest
from pathlib import Path

from nanobot.ene.social.person import PersonRegistry
from nanobot.ene.social.graph import SocialGraph


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
def populated(registry: PersonRegistry, graph: SocialGraph):
    """Create a small social network: Dad — Alice — Bob — Charlie."""
    alice = registry.create("discord:100", "Alice")
    bob = registry.create("discord:101", "Bob")
    charlie = registry.create("discord:102", "Charlie")

    # Dad — Alice (friend)
    registry.add_connection("p_dad001", alice.id, "friend", "IRL")
    # Alice — Bob (acquaintance)
    registry.add_connection(alice.id, bob.id, "acquaintance", "Server")
    # Bob — Charlie (friend)
    registry.add_connection(bob.id, charlie.id, "friend", "Gaming")

    return {"alice": alice, "bob": bob, "charlie": charlie}


# ── Tests ─────────────────────────────────────────────────


class TestGetConnections:
    def test_get_connections(self, graph, populated):
        conns = graph.get_connections(populated["alice"].id)
        ids = {c.person_id for c in conns}
        assert "p_dad001" in ids
        assert populated["bob"].id in ids

    def test_no_connections(self, graph, registry):
        lonely = registry.create("discord:999", "Lonely")
        conns = graph.get_connections(lonely.id)
        assert conns == []

    def test_unknown_person(self, graph):
        conns = graph.get_connections("p_nonexistent")
        assert conns == []


class TestMutualConnections:
    def test_mutual(self, graph, populated):
        # Dad and Bob are both connected to Alice
        mutuals = graph.get_mutual_connections(
            "p_dad001", populated["bob"].id
        )
        assert populated["alice"].id in mutuals

    def test_no_mutual(self, graph, populated):
        # Dad and Charlie have no mutual connections
        mutuals = graph.get_mutual_connections(
            "p_dad001", populated["charlie"].id
        )
        assert mutuals == []


class TestConnectionChain:
    def test_direct_connection(self, graph, populated):
        chain = graph.get_connection_chain("p_dad001", populated["alice"].id)
        assert chain is not None
        assert chain[0] == "p_dad001"
        assert chain[-1] == populated["alice"].id
        assert len(chain) == 2

    def test_two_hop_connection(self, graph, populated):
        chain = graph.get_connection_chain("p_dad001", populated["bob"].id)
        assert chain is not None
        assert len(chain) == 3  # Dad → Alice → Bob

    def test_three_hop_connection(self, graph, populated):
        chain = graph.get_connection_chain("p_dad001", populated["charlie"].id)
        assert chain is not None
        assert len(chain) == 4  # Dad → Alice → Bob → Charlie

    def test_no_connection(self, graph, registry):
        isolated = registry.create("discord:888", "Isolated")
        chain = graph.get_connection_chain("p_dad001", isolated.id)
        assert chain is None

    def test_self_connection(self, graph):
        chain = graph.get_connection_chain("p_dad001", "p_dad001")
        assert chain == ["p_dad001"]

    def test_max_depth_exceeded(self, graph, populated):
        # Dad → Alice → Bob → Charlie is depth 3
        chain = graph.get_connection_chain(
            "p_dad001", populated["charlie"].id, max_depth=2
        )
        assert chain is None


class TestRenderForContext:
    def test_render(self, graph, populated):
        text = graph.render_for_context(populated["alice"].id)
        assert "Dad" in text
        assert "Bob" in text
        assert "friend" in text or "acquaintance" in text

    def test_render_no_connections(self, graph, registry):
        lonely = registry.create("discord:777", "Lonely")
        text = graph.render_for_context(lonely.id)
        assert text == ""

    def test_render_format(self, graph, populated):
        text = graph.render_for_context(populated["alice"].id)
        assert text.startswith("Connected to:")
