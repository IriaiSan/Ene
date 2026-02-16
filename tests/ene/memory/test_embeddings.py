"""Tests for EneEmbeddings — embedding provider for Ene's vector memory."""

import pytest
from unittest.mock import patch, MagicMock

from nanobot.ene.memory.embeddings import EneEmbeddings


def _make_litellm_response(vectors: list[list[float]]):
    """Create a mock litellm embedding response."""
    resp = MagicMock()
    resp.data = [{"embedding": v} for v in vectors]
    return resp


# ── Basic Embedding ────────────────────────────────────────


def test_embed_returns_vectors():
    """embed() should return one vector per input text."""
    fake_vectors = [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]

    with patch("litellm.embedding", return_value=_make_litellm_response(fake_vectors)):
        emb = EneEmbeddings(model="test-model", api_key="test-key")
        result = emb.embed(["hello", "world"])

    assert len(result) == 2
    assert result[0] == [0.1, 0.2, 0.3]
    assert result[1] == [0.4, 0.5, 0.6]


def test_embed_empty_list():
    """embed([]) should return an empty list without calling API."""
    emb = EneEmbeddings()
    result = emb.embed([])
    assert result == []


def test_embed_passes_correct_kwargs():
    """embed() should pass model, api_key, and api_base to litellm."""
    fake_vectors = [[0.1, 0.2]]
    mock_resp = _make_litellm_response(fake_vectors)

    with patch("litellm.embedding", return_value=mock_resp) as mock_embed:
        emb = EneEmbeddings(
            model="openai/text-embedding-3-small",
            api_key="sk-test123",
            api_base="https://api.example.com",
        )
        emb.embed(["test"])

        mock_embed.assert_called_once_with(
            model="openai/text-embedding-3-small",
            input=["test"],
            api_key="sk-test123",
            api_base="https://api.example.com",
        )


def test_embed_detects_dimension():
    """First successful embed should detect and store the dimension."""
    fake_vectors = [[0.1] * 1536]

    with patch("litellm.embedding", return_value=_make_litellm_response(fake_vectors)):
        emb = EneEmbeddings(model="test-model")
        assert emb.dimension is None
        emb.embed(["test"])
        assert emb.dimension == 1536


# ── Fallback ───────────────────────────────────────────────


def test_embed_fallback_on_api_failure():
    """When litellm fails, should fall back to ChromaDB default embeddings."""
    import numpy as np

    fallback_vectors = [np.array([0.1, 0.2, 0.3])]

    with patch("litellm.embedding", side_effect=Exception("API down")):
        with patch(
            "chromadb.utils.embedding_functions.DefaultEmbeddingFunction"
        ) as MockFn:
            mock_fn_instance = MagicMock()
            mock_fn_instance.return_value = fallback_vectors
            MockFn.return_value = mock_fn_instance

            emb = EneEmbeddings(model="test-model")
            result = emb.embed(["test"])

    assert len(result) == 1
    assert result[0] == [0.1, 0.2, 0.3]


def test_embed_raises_when_both_fail():
    """When both litellm and fallback fail, should raise RuntimeError."""
    with patch("litellm.embedding", side_effect=Exception("API down")):
        with patch(
            "chromadb.utils.embedding_functions.DefaultEmbeddingFunction",
            side_effect=Exception("Fallback also broken"),
        ):
            emb = EneEmbeddings(model="test-model")
            with pytest.raises(RuntimeError, match="All embedding methods failed"):
                emb.embed(["test"])


def test_embed_no_api_key_still_works():
    """embed() without api_key should not include it in kwargs."""
    fake_vectors = [[0.1, 0.2]]
    mock_resp = _make_litellm_response(fake_vectors)

    with patch("litellm.embedding", return_value=mock_resp) as mock_embed:
        emb = EneEmbeddings(model="test-model")
        emb.embed(["test"])

        call_kwargs = mock_embed.call_args[1]
        assert "api_key" not in call_kwargs
        assert "api_base" not in call_kwargs
