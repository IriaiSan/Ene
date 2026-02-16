"""EneEmbeddings — embedding provider for Ene's vector memory.

Uses litellm.embedding() with a configurable model (default: OpenAI
text-embedding-3-small). Falls back to ChromaDB's default embedding
function if the API call fails.
"""

from __future__ import annotations

from typing import Any

from loguru import logger


class EneEmbeddings:
    """Thin wrapper around litellm for embedding generation.

    Usage:
        emb = EneEmbeddings(model="openai/text-embedding-3-small", api_key="...")
        vectors = emb.embed(["hello world", "goodbye"])
        # vectors: list[list[float]], one per input text
    """

    def __init__(
        self,
        model: str = "openai/text-embedding-3-small",
        api_key: str | None = None,
        api_base: str | None = None,
    ):
        self.model = model
        self.api_key = api_key
        self.api_base = api_base
        self._dimension: int | None = None

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a list of texts into vectors.

        Args:
            texts: List of strings to embed.

        Returns:
            List of embedding vectors (one per input text).

        Raises:
            RuntimeError: If both the primary API and fallback fail.
        """
        if not texts:
            return []

        try:
            return self._embed_via_litellm(texts)
        except Exception as e:
            logger.warning(f"Embedding API failed ({e}), falling back to default")
            try:
                return self._embed_fallback(texts)
            except Exception as e2:
                logger.error(f"Fallback embedding also failed: {e2}")
                raise RuntimeError(
                    f"All embedding methods failed. Primary: {e}, Fallback: {e2}"
                ) from e2

    def _embed_via_litellm(self, texts: list[str]) -> list[list[float]]:
        """Embed using litellm (calls OpenAI-compatible API)."""
        import litellm

        kwargs: dict[str, Any] = {
            "model": self.model,
            "input": texts,
        }
        if self.api_key:
            kwargs["api_key"] = self.api_key
        if self.api_base:
            kwargs["api_base"] = self.api_base

        response = litellm.embedding(**kwargs)
        # Ensure native Python floats (some providers return numpy types)
        vectors = [[float(x) for x in item["embedding"]] for item in response.data]

        if self._dimension is None and vectors:
            self._dimension = len(vectors[0])
            logger.info(f"Embedding dimension detected: {self._dimension}")

        return vectors

    def _embed_fallback(self, texts: list[str]) -> list[list[float]]:
        """Fallback: use ChromaDB's default embedding function."""
        from chromadb.utils.embedding_functions import DefaultEmbeddingFunction

        fn = DefaultEmbeddingFunction()
        results = fn(texts)
        # DefaultEmbeddingFunction returns list of numpy arrays or lists.
        # numpy arrays have float32 elements which ChromaDB rejects —
        # must convert to native Python float.
        return [[float(x) for x in v] for v in results]

    @property
    def dimension(self) -> int | None:
        """Return the detected embedding dimension (None if not yet known)."""
        return self._dimension
