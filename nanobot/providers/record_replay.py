"""Record/replay LLM provider for deterministic, zero-cost lab testing.

Wraps a real LLMProvider. Four modes:
- record:         pass through to real provider, save response to cache
- replay:         return cached response, raise on cache miss
- replay_or_live: try cache first, fall back to real provider on miss
- passthrough:    always call real provider, no caching

Cache key: SHA-256 hash of (model + message roles/content + tool names).
Excludes temperature/max_tokens so the same prompt gets the same cache hit
regardless of behavioral parameters.

Based on Docker Cagent VCR pattern + Block Engineering TestProvider.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from loguru import logger

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


CacheMode = Literal["record", "replay", "replay_or_live", "passthrough"]


@dataclass
class CacheStats:
    """Track cache hit/miss/record counts."""
    hits: int = 0
    misses: int = 0
    records: int = 0
    errors: int = 0

    @property
    def total(self) -> int:
        return self.hits + self.misses + self.records + self.errors

    def as_dict(self) -> dict[str, int]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "records": self.records,
            "errors": self.errors,
            "total": self.total,
        }


class CacheMissError(Exception):
    """Raised in replay mode when no cached response exists for a request."""
    pass


class RecordReplayProvider(LLMProvider):
    """LLM provider that records and replays responses from a cache.

    Wraps a real provider and intercepts chat() calls to:
    - Serve cached responses (replay)
    - Record new responses to cache (record)
    - Do both (replay_or_live)
    - Pass through without caching (passthrough)

    Usage:
        real = LiteLLMProvider(...)
        provider = RecordReplayProvider(
            real_provider=real,
            cache_dir=Path("~/.nanobot-lab/cache/llm_responses"),
            mode="replay_or_live",
        )
        response = await provider.chat(messages, tools, model)
        print(provider.stats)
    """

    def __init__(
        self,
        real_provider: LLMProvider,
        cache_dir: Path,
        mode: CacheMode = "replay_or_live",
    ):
        # Don't call super().__init__() with keys — we delegate to real_provider
        super().__init__(api_key=None, api_base=None)
        self._real = real_provider
        self._cache_dir = Path(cache_dir)
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self.mode: CacheMode = mode
        self.stats = CacheStats()

    def get_default_model(self) -> str:
        """Delegate to the real provider."""
        return self._real.get_default_model()

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> LLMResponse:
        """Chat with cache layer.

        Cache key is based on model + messages + tools only.
        max_tokens and temperature are excluded so the same semantic
        request always hits the same cache entry.
        """
        resolved_model = model or self.get_default_model()
        cache_key = self._hash_request(resolved_model, messages, tools)

        if self.mode == "passthrough":
            return await self._call_real(
                messages, tools, resolved_model, max_tokens, temperature
            )

        if self.mode == "record":
            response = await self._call_real(
                messages, tools, resolved_model, max_tokens, temperature
            )
            self._save_to_cache(cache_key, response, resolved_model, messages)
            self.stats.records += 1
            return response

        # replay or replay_or_live — try cache first
        cached = self._load_from_cache(cache_key)
        if cached is not None:
            self.stats.hits += 1
            logger.debug(f"Cache HIT: {cache_key[:12]}...")
            return cached

        if self.mode == "replay":
            self.stats.misses += 1
            last_user = self._extract_last_user_msg(messages)
            raise CacheMissError(
                f"No cached response for hash {cache_key[:16]}... "
                f"(model={resolved_model}, last_user_msg={last_user[:80]!r})"
            )

        # replay_or_live — fall back to real provider
        self.stats.misses += 1
        logger.debug(f"Cache MISS, calling real provider: {cache_key[:12]}...")
        response = await self._call_real(
            messages, tools, resolved_model, max_tokens, temperature
        )
        self._save_to_cache(cache_key, response, resolved_model, messages)
        self.stats.records += 1
        return response

    # ── Hashing ───────────────────────────────────────────

    @staticmethod
    def _hash_request(
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> str:
        """Deterministic hash of the request for cache keying.

        Includes: model, message roles + content, tool names.
        Excludes: temperature, max_tokens, timestamps, tool schemas.
        """
        parts: list[str] = [f"model:{model}"]

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")
            # Normalize content for consistency
            if isinstance(content, list):
                # Multi-modal messages — serialize deterministically
                content = json.dumps(content, sort_keys=True, ensure_ascii=False)
            parts.append(f"{role}:{content}")

        if tools:
            # Only hash tool names, not full schemas (schema changes
            # shouldn't invalidate cache for same functionality)
            tool_names = sorted(
                t.get("function", {}).get("name", "")
                for t in tools
                if isinstance(t, dict)
            )
            parts.append(f"tools:{','.join(tool_names)}")

        raw = "\n".join(parts)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    # ── Cache I/O ─────────────────────────────────────────

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _save_to_cache(
        self,
        key: str,
        response: LLMResponse,
        model: str,
        messages: list[dict[str, Any]],
    ) -> None:
        """Persist a response to the cache directory."""
        data: dict[str, Any] = {
            "content": response.content,
            "finish_reason": response.finish_reason,
            "usage": response.usage,
            "reasoning_content": response.reasoning_content,
            "tool_calls": [
                {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                for tc in response.tool_calls
            ],
            # Debug metadata (not used for hashing)
            "_hash": key,
            "_model": model,
            "_last_user_msg": self._extract_last_user_msg(messages),
            "_message_count": len(messages),
        }
        try:
            path = self._cache_path(key)
            path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as e:
            logger.error(f"Failed to save cache entry {key[:12]}: {e}")
            self.stats.errors += 1

    def _load_from_cache(self, key: str) -> LLMResponse | None:
        """Load a cached response, or None if not found."""
        path = self._cache_path(key)
        if not path.exists():
            return None

        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            tool_calls = [
                ToolCallRequest(
                    id=tc["id"],
                    name=tc["name"],
                    arguments=tc["arguments"],
                )
                for tc in data.get("tool_calls", [])
            ]
            return LLMResponse(
                content=data.get("content"),
                tool_calls=tool_calls,
                finish_reason=data.get("finish_reason", "stop"),
                usage=data.get("usage", {}),
                reasoning_content=data.get("reasoning_content"),
            )
        except Exception as e:
            logger.error(f"Failed to load cache entry {key[:12]}: {e}")
            self.stats.errors += 1
            return None

    # ── Real provider delegation ──────────────────────────

    async def _call_real(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        """Call the wrapped real provider."""
        return await self._real.chat(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
        )

    # ── Helpers ───────────────────────────────────────────

    @staticmethod
    def _extract_last_user_msg(messages: list[dict[str, Any]]) -> str:
        """Extract the last user message for debug metadata."""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content[:200]
                return str(content)[:200]
        return ""

    def clear_cache(self) -> int:
        """Delete all cached responses. Returns count deleted."""
        count = 0
        for path in self._cache_dir.glob("*.json"):
            try:
                path.unlink()
                count += 1
            except Exception as e:
                logger.error(f"Failed to delete cache file {path.name}: {e}")
        return count

    def cache_size(self) -> int:
        """Number of cached responses."""
        return len(list(self._cache_dir.glob("*.json")))
