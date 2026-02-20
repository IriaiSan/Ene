"""PromptLoader — load and substitute prompt templates from .txt files.

Centralized prompt management for version tracking and observability.
Prompts are stored as .txt files alongside this module.
Template variables use {name} syntax and are substituted via format_map.

Usage::

    loader = PromptLoader()
    prompt = loader.load("diary_system")
    prompt = loader.load("summary_update", existing_summary="...", older_text="...")
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loguru import logger


class _SafeDict(dict):
    """Dict that returns '{key}' for missing keys instead of raising KeyError.

    Allows partial substitution — template vars that aren't provided
    stay as literal {name} in the output instead of crashing.
    """
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


class PromptLoader:
    """Load prompt templates from nanobot/agent/prompts/*.txt.

    Features:
        - File-based templates with {variable} substitution
        - In-memory cache (cleared on reload)
        - Version tracking from manifest.json
        - Safe partial substitution (missing vars stay as {name})
    """

    def __init__(self, prompts_dir: Path | None = None) -> None:
        self._dir = prompts_dir or Path(__file__).parent
        self._cache: dict[str, str] = {}
        self._version: str | None = None
        self._manifest: dict | None = None

    def load(self, prompt_name: str, **template_vars: Any) -> str:
        """Load a prompt template and substitute variables.

        Args:
            prompt_name: Prompt name (matches manifest key and filename without .txt).
            **template_vars: Variables to substitute in the template.

        Returns:
            Substituted prompt string.

        Raises:
            FileNotFoundError: If the prompt file doesn't exist.
        """
        raw = self.load_raw(prompt_name)
        if not template_vars:
            return raw
        return raw.format_map(_SafeDict(template_vars))

    def load_raw(self, prompt_name: str) -> str:
        """Load a prompt template without any substitution.

        Args:
            prompt_name: Prompt name.

        Returns:
            Raw template string.

        Raises:
            FileNotFoundError: If the prompt file doesn't exist.
        """
        if prompt_name in self._cache:
            return self._cache[prompt_name]

        path = self._dir / f"{prompt_name}.txt"
        if not path.exists():
            raise FileNotFoundError(f"Prompt '{prompt_name}' not found at {path}")

        text = path.read_text(encoding="utf-8")
        self._cache[prompt_name] = text
        return text

    @property
    def version(self) -> str:
        """Read version from manifest.json."""
        if self._version is not None:
            return self._version

        manifest_path = self._dir / "manifest.json"
        if manifest_path.exists():
            try:
                data = json.loads(manifest_path.read_text(encoding="utf-8"))
                self._version = data.get("version", "unknown")
            except Exception:
                self._version = "unknown"
        else:
            self._version = "unknown"

        return self._version

    @property
    def manifest(self) -> dict:
        """Load and cache the manifest."""
        if self._manifest is not None:
            return self._manifest

        manifest_path = self._dir / "manifest.json"
        if manifest_path.exists():
            try:
                self._manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                self._manifest = {}
        else:
            self._manifest = {}

        return self._manifest

    def reload(self) -> None:
        """Clear all caches — forces re-read from disk on next access.

        Useful for live prompt editing or testing.
        """
        self._cache.clear()
        self._version = None
        self._manifest = None

    def list_prompts(self) -> list[str]:
        """List all available prompt names from manifest."""
        return list(self.manifest.get("prompts", {}).keys())
