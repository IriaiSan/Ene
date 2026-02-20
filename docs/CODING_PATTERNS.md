# Ene — Coding Patterns

> These patterns come from upstream nanobot, extended for Ene's modules.
> Follow them exactly. They are enforced by WHITELIST decisions.

**The golden rule:** When adding new code, find the closest existing file and match its patterns. When in doubt, copy the existing file's structure over inventing your own.

**Upstream alignment:** This project is a fork of [nanobot](https://github.com/HKUDS/nanobot). Upstream files (tools, bus, session, channels/base, providers) set the baseline patterns. Ene-specific files (`nanobot/ene/`, `nanobot/lab/`) extend those patterns with `from __future__ import annotations` and `TYPE_CHECKING` blocks for the module system's circular import avoidance. When in doubt, match upstream.

---

## Import Order

### Upstream style (tools, channels, bus, session, providers)

```python
"""Module docstring — one-line or short paragraph."""

import json
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.agent.tools.base import Tool
```

### Ene module style (nanobot/ene/*, nanobot/lab/*)

```python
"""Module docstring — one-line or short paragraph."""

from __future__ import annotations

import time                                 # stdlib
from pathlib import Path
from typing import Any, TYPE_CHECKING

from loguru import logger                   # third-party

from nanobot.ene import EneModule, EneContext  # project imports

if TYPE_CHECKING:                           # type-only imports last
    from nanobot.ene.observatory.store import MetricsStore
```

Rules:
- `from __future__ import annotations` used in Ene modules (needed for `TYPE_CHECKING` pattern)
- Upstream files don't use it — don't add it to upstream-origin files
- Stdlib -> typing -> third-party (loguru) -> project
- `if TYPE_CHECKING:` block at the bottom for circular import avoidance
- No wildcard imports — always explicit (WHITELIST S4)
- Loguru is the only logger — never `import logging`
- `pathlib.Path` — never `os.path` (upstream pattern)

---

## Docstrings

Follows upstream nanobot conventions (Google-style).

### File Docstrings

```python
"""Social tools — update_person_note, view_person, list_people.

These tools let Ene manage her knowledge about people.
All tools share a reference to the PersonRegistry.
"""
```

- Every `.py` file has a module docstring
- First line is a self-contained summary
- Optional: design choices, research citations (paper name + year)

### Class Docstrings

```python
class SocialModule(EneModule):
    """Social module for Ene — manages people, trust, and connections.

    Lifecycle:
        1. initialize() — creates social dir, loads registry, ensures Dad exists
        2. get_tools() — returns update_person_note, view_person, list_people
        3. get_context_block() — returns social awareness guidance
        4. on_message(msg, responded) — records interaction, updates signals
    """
```

- Every public class has a docstring
- Lifecycle or usage pattern described for complex classes

### Method Docstrings

**Interfaces / ABCs** — use Google-style Args/Returns (matches upstream `BaseChannel`, `Tool`):

```python
def _handle_message(
    self,
    sender_id: str,
    chat_id: str,
    content: str,
    media: list[str] | None = None,
) -> None:
    """Handle an incoming message from the chat platform.

    Args:
        sender_id: The sender's identifier.
        chat_id: The chat/channel identifier.
        content: Message text content.
        media: Optional list of media URLs.
    """
```

**Implementations** — one-line docstrings (matches upstream tool files):

```python
async def execute(self, **kwargs: Any) -> str:
    """Save a new entry to the specified core memory section."""
```

Rules:
- Interfaces and ABCs get `Args:` / `Returns:` sections (Google-style)
- Implementation methods get one-line docstrings
- Private methods (`_underscore`) only need docstrings if non-obvious
- Explain WHY, not WHAT (the code shows what)

---

## Error Handling

Three strategies. Each has a specific use case.

### Strategy 1: Return error string — Tools

```python
async def execute(self, **kwargs: Any) -> str:
    name = kwargs.get("person_name", "").strip()
    if not name:
        return "Error: person_name is required."

    profile = self._registry.find_by_name(name)
    if profile is None:
        return f"I don't know anyone named '{name}'."

    return f"Updated {profile.display_name}."
```

**When:** Tool `execute()` methods. The LLM reads the return value and needs human-readable feedback, not stack traces.

### Strategy 2: Raise exception — Core infrastructure

```python
def load(self, prompt_name: str, **template_vars: Any) -> str:
    raw = self._cache.get(prompt_name)
    if raw is None:
        raise FileNotFoundError(f"Prompt '{prompt_name}' not found in {self._dir}")
    return raw.format_map(_SafeDict(template_vars))
```

**When:** Programming errors that should crash loudly. Missing files, invalid config, broken invariants. If this fires, something is genuinely wrong.

### Strategy 3: Log + continue — Module hooks, optional features

```python
async def on_message(self, msg: "InboundMessage", responded: bool) -> None:
    try:
        self._registry.record_interaction(platform_id, responded)
    except Exception:
        logger.warning("Social: failed to record interaction for {}", platform_id, exc_info=True)
```

**When:** Non-critical operations where failure shouldn't kill the bot. Module lifecycle hooks, metrics recording, background tasks.

### Never Do

- `except:` without specifying a type — always `except Exception:` minimum
- `except Exception: pass` — always log something
- `raise` inside tool `execute()` — return an error string instead
- Let metrics/observatory errors propagate — they should never crash the pipeline

---

## Logging Levels

| Level | When | Example |
|-------|------|---------|
| `logger.debug()` | Internal state, high volume, hot paths | Feature scores, thread assignments |
| `logger.info()` | Significant lifecycle events, low volume | Module initialized, session rotated, model fallback |
| `logger.warning()` | Something wrong but bot continues | Failed to record metric, non-critical timeout |
| `logger.error()` | Something broke that needs attention | LLM call failed, session write failed |

Rules:
- Never `info` in hot paths (per-message processing) — use `debug`
- Prefix with module name: `"Memory: ..."`, `"Social: ..."`, `"Tracker: ..."`
- Include relevant context: `logger.warning("Social: failed for {}", person_id)`
- `warning` = recovered. `error` = needs fixing.

---

## Template: New Tool

```python
"""Brief description of these tools."""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.agent.tools.base import Tool

if TYPE_CHECKING:
    from nanobot.x.y import Dependency


class MyTool(Tool):
    """One-line description of what this tool does."""

    def __init__(self, dep: "Dependency") -> None:
        self._dep = dep

    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return (
            "Description the LLM sees when deciding whether to use this tool. "
            "Explain what it does and when to use it."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "param_name": {
                    "type": "string",
                    "description": "What this parameter does.",
                },
            },
            "required": ["param_name"],
        }

    async def execute(self, **kwargs: Any) -> str:
        """Do the thing."""
        value = kwargs.get("param_name", "").strip()
        if not value:
            return "Error: param_name is required."

        try:
            result = self._dep.do_thing(value)
            return f"Done: {result}"
        except Exception:
            logger.warning("MyTool: failed for %s", value, exc_info=True)
            return "Error: something went wrong."
```

### Checklist for new tools

- [ ] Inherits `Tool` ABC
- [ ] `execute()` returns strings, never raises
- [ ] Parameters use JSON Schema format
- [ ] Dependencies injected via `__init__`, not imported globally
- [ ] If Dad-only: add tool name to `RESTRICTED_TOOLS` in `security.py`
- [ ] Registered in the module's `get_tools()` method
- [ ] Test file at `tests/` mirroring source path

---

## Template: New Module

### File structure

```
nanobot/ene/mymodule/
    __init__.py     # MyModule(EneModule) — entry point
    core.py         # Business logic
    tools.py        # Tool classes (optional, if module has tools)
    models.py       # Dataclasses (optional, if complex data structures)
```

### Module class (`__init__.py`)

```python
"""Ene MyModule — Module N of the Ene subsystem architecture.

What this module does in 1-2 sentences.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from loguru import logger

from nanobot.ene import EneModule, EneContext

if TYPE_CHECKING:
    from nanobot.agent.tools.base import Tool
    from nanobot.bus.events import InboundMessage


class MyModule(EneModule):
    """My module for Ene — one-line summary.

    Lifecycle:
        1. initialize() — set up resources
        2. get_tools() — return tool list
        3. get_context_block() — static system prompt text
        4. on_message(msg, responded) — post-message hook
    """

    def __init__(self) -> None:
        self._core: Any = None        # MyCore, set in initialize()
        self._ctx: EneContext | None = None

    @property
    def name(self) -> str:
        return "mymodule"

    async def initialize(self, ctx: EneContext) -> None:
        """Initialize the module."""
        from nanobot.ene.mymodule.core import MyCore  # late-bind heavy imports

        self._ctx = ctx
        data_dir = ctx.workspace / "memory" / "mymodule"
        data_dir.mkdir(parents=True, exist_ok=True)
        self._core = MyCore(data_dir)
        logger.info("MyModule: initialized")

    def get_tools(self) -> list["Tool"]:
        """Return tools this module provides."""
        from nanobot.ene.mymodule.tools import MyTool
        return [MyTool(self._core)]

    def get_context_block(self) -> str | None:
        """Return text injected into every system prompt."""
        return None  # or return a string block

    async def on_message(self, msg: "InboundMessage", responded: bool) -> None:
        """Called after every message (lurked or responded)."""
        try:
            self._core.process(msg)
        except Exception:
            logger.warning("MyModule: on_message failed", exc_info=True)
```

### Checklist for new modules

- [ ] Inherits `EneModule` ABC
- [ ] Late-bind heavy imports in `initialize()`, not at top-level
- [ ] State persists to disk — JSON or SQLite (WHITELIST A6)
- [ ] Register in `AgentLoop._register_ene_modules()` (loop.py is LOCKED — needs Dad's approval)
- [ ] Matching test directory: `tests/ene/mymodule/`
- [ ] WHITELIST entry if architectural decision involved
- [ ] CHANGELOG entry

---

## Template: New Test File

```python
"""Tests for mymodule.core."""

from __future__ import annotations

import pytest

from nanobot.ene.mymodule.core import MyClass


@pytest.fixture
def instance(tmp_path):
    """Create isolated instance for testing."""
    return MyClass(data_dir=tmp_path)


class TestBasicOperations:
    def test_create_thing(self, instance):
        result = instance.create("test")
        assert result.name == "test"

    def test_create_empty_name_rejected(self, instance):
        result = instance.create("")
        assert result is None

    def test_duplicate_name_raises(self, instance):
        instance.create("test")
        with pytest.raises(ValueError, match="already exists"):
            instance.create("test")


class TestEdgeCases:
    def test_unicode_names(self, instance):
        result = instance.create("test")
        assert result is not None

    def test_persistence(self, instance, tmp_path):
        instance.create("test")
        # Reload from disk
        reloaded = MyClass(data_dir=tmp_path)
        assert reloaded.get("test") is not None
```

### Rules

- `tmp_path` for all file I/O — never touch real paths (WHITELIST T7)
- `FakeProvider` for any LLM calls — never real API calls (WHITELIST T4)
- Group related tests in classes named `TestDescriptiveName`
- Test method names: `test_what_condition_expected`
- Bare `assert` for simple checks
- `pytest.raises(ExceptionType, match="message")` for expected exceptions
- No `unittest.TestCase` — pytest only (WHITELIST T3)
- Every new source file gets a corresponding test file (WHITELIST T1)

---

## Pattern: Module Metrics

When instrumenting a module with observability events (WHITELIST A15):

```python
# At module level (NOT inside a class):
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nanobot.ene.observatory.module_metrics import ModuleMetrics

_metrics: "ModuleMetrics | None" = None


def set_metrics(m: "ModuleMetrics") -> None:
    """Called by loop.py during module initialization."""
    global _metrics
    _metrics = m
```

Then in the code being instrumented:

```python
# Fire-and-forget event
if _metrics:
    _metrics.record("event_name", channel_key, key=value, count=42)

# Paired span with automatic duration tracking
if _metrics:
    with _metrics.span("context_built", channel_key) as data:
        result = do_expensive_work()
        data["items"] = len(result)  # enrich span data
```

Wiring in `loop.py` `_initialize_ene_modules()`:

```python
from nanobot.ene.mymodule import core as mymodule_core
my_metrics = ModuleMetrics("mymodule", store, self._live)
mymodule_core.set_metrics(my_metrics)
self._module_metrics["mymodule"] = my_metrics
```

---

## Anti-Patterns

| DON'T | DO | Why |
|-------|-----|-----|
| `import logging` | `from loguru import logger` | One logger across the project |
| `except: pass` | `except Exception: logger.warning(...)` | Silent failures = invisible bugs |
| `raise ValueError` in tool `execute()` | `return "Error: ..."` | LLM needs readable feedback |
| `from x import *` | `from x import Y, Z` | Explicit imports (WHITELIST S4) |
| `self.thing = None` | `self._thing: Type \| None = None` | Private prefix + type hint |
| Heavy imports at module top-level | Import inside `initialize()` | Avoids circular imports, speeds startup |
| Magic numbers in code | Constants in dedicated module | (WHITELIST S6) |
| `os.path.join(a, b)` | `Path(a) / b` | pathlib is upstream pattern |
| `open(path)` | `open(path, encoding="utf-8")` | Windows crashes on non-ASCII |
| Add `__future__` to upstream files | Only use in `nanobot/ene/` and `nanobot/lab/` | Don't modify upstream-origin file patterns |
| In-memory-only state | Persist to disk (JSON/SQLite) | Restarts shouldn't lose data (WHITELIST A6) |
| Modify locked files without approval | Ask Dad first | Core systems LOCKED (WHITELIST A3) |
| `print()` for debugging | `logger.debug()` | Dashboard captures loguru, not print |
