# Ene Testing Guide

Complete guide to testing, the development lab, and quality assurance.

---

## Quick Reference

```bash
# Run all tests (must pass before any commit)
cd C:\Users\Ene\Ene
python -m pytest tests/ -x -q

# Run a specific module's tests
python -m pytest tests/ene/social/ -q
python -m pytest tests/ene/memory/ -q
python -m pytest tests/ene/conversation/ -q
python -m pytest tests/lab/ -q

# Run a single test file
python -m pytest tests/lab/test_state.py -x -q

# Run a single test by name
python -m pytest tests/ene/social/test_trust.py::TestDadBypass::test_dad_discord -x

# Run with verbose output (see test names)
python -m pytest tests/ -v

# Run with output capture disabled (see print statements)
python -m pytest tests/ -x -s
```

**Current count: ~863 tests. All must pass before commit.**

---

## Test Organization

Tests mirror the source tree. Every source file should have a corresponding test file.

```
nanobot/                            tests/
├── agent/                          ├── (agent tests are inline / TODO)
├── channels/                       ├── channels/
│   ├── mock.py                     │   └── test_mock.py
├── ene/                            ├── ene/
│   ├── conversation/               │   ├── conversation/
│   │   ├── tracker.py              │   │   ├── test_tracker.py
│   │   ├── formatter.py            │   │   ├── test_formatter.py
│   │   ├── signals.py              │   │   ├── test_signals.py
│   │   ├── models.py               │   │   ├── test_models.py
│   │   └── storage.py              │   │   └── test_storage.py
│   ├── daemon/                     │   ├── daemon/
│   │   ├── processor.py            │   │   ├── test_processor.py
│   │   └── models.py               │   │   └── test_models.py
│   ├── memory/                     │   ├── memory/
│   │   ├── core_memory.py          │   │   ├── test_core_memory.py
│   │   ├── vector_memory.py        │   │   ├── test_vector_memory.py
│   │   ├── system.py               │   │   ├── test_system.py
│   │   ├── sleep_agent.py          │   │   ├── test_sleep_agent.py
│   │   ├── embeddings.py           │   │   ├── test_embeddings.py
│   │   └── tools.py                │   │   └── test_tools.py
│   ├── observatory/                │   ├── observatory/
│   │   └── collector.py            │   │   ├── test_collector.py
│   │                               │   │   ├── test_pricing.py
│   │                               │   │   └── test_store.py
│   └── social/                     │   ├── social/
│       ├── person.py               │   │   ├── test_person.py
│       ├── trust.py                │   │   ├── test_trust.py
│       ├── graph.py                │   │   ├── test_graph.py
│       ├── tools.py                │   │   └── test_tools.py
│       └── module.py               │   │   └── test_module.py
├── lab/                            ├── lab/
│   ├── state.py                    │   ├── test_state.py
│   ├── harness.py                  │   ├── test_harness.py
│   ├── audit.py                    │   ├── test_audit.py
│   └── stress.py                   │   └── test_stress.py
├── providers/                      ├── providers/
│   └── record_replay.py            │   └── test_record_replay.py
└── session/                        └── (session tests are inline / TODO)
```

**Rule (WHITELIST T2):** When you create `nanobot/foo/bar.py`, create `tests/foo/test_bar.py`.

---

## Writing Tests

### Test File Structure

Every test file follows this pattern:

```python
"""Tests for [module name] — [what it covers].

Brief description of edge cases or focus areas.
"""

import pytest

from nanobot.module.thing import ThingUnderTest


# ── Fixtures ──────────────────────────────────────────────

@pytest.fixture
def my_fixture(tmp_path):
    """Description of what this fixture provides."""
    # Setup
    thing = ThingUnderTest(path=tmp_path)
    yield thing
    # Teardown (if needed)


# ── Section Name ──────────────────────────────────────────

def test_specific_behavior(my_fixture):
    """One-line description of what this test verifies."""
    result = my_fixture.do_something()
    assert result == expected


class TestGroupedBehavior:
    """Group related tests into classes when there are many."""

    def test_case_a(self):
        ...

    def test_case_b(self):
        ...
```

### Key Patterns

**1. Use `tmp_path` for file system isolation:**
```python
def test_save_to_disk(tmp_path):
    """File operations use tmp_path, never real paths."""
    manager = SessionManager(workspace=tmp_path, sessions_dir=tmp_path / "sessions")
    # ... test writes go to tmp_path, cleaned up automatically
```

**2. Use `set_data_path()` / `set_lab_root()` for global state isolation:**
```python
@pytest.fixture(autouse=True)
def isolated(tmp_path):
    set_data_path(tmp_path)
    yield
    set_data_path(None)  # Always reset
```

**3. Mock LLM providers — never make real API calls (WHITELIST T4):**
```python
class FakeProvider(LLMProvider):
    """Returns canned responses. No API calls."""
    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.call_count += 1
        return LLMResponse(content="fake response")

    def get_default_model(self) -> str:
        return "fake/model"
```

**4. Use `@pytest.mark.asyncio` for async tests:**
```python
@pytest.mark.asyncio
async def test_async_operation():
    result = await some_async_function()
    assert result is not None
```

**5. Test edge cases explicitly:**
```python
def test_empty_input():
    """Empty input should return empty, not crash."""
    assert process("") == ""

def test_none_input():
    """None input should be handled gracefully."""
    with pytest.raises(ValueError):
        process(None)

def test_unicode():
    """Non-ASCII content should work on Windows."""
    assert process("こんにちは") == expected
```

**6. Test both the happy path and failure modes:**
```python
def test_create_snapshot_success(fake_live):
    path = create_snapshot("test", source="live")
    assert path.exists()

def test_create_snapshot_duplicate_raises(fake_live):
    create_snapshot("dup", source="live")
    with pytest.raises(ValueError, match="already exists"):
        create_snapshot("dup", source="live")

def test_create_snapshot_missing_source():
    with pytest.raises(FileNotFoundError):
        create_snapshot("bad", source="run:nonexistent")
```

### What to Test

For any new code, test:

| Category | What to verify | Example |
|----------|---------------|---------|
| **Happy path** | Normal operation works | `create_run("name")` → dirs created |
| **Edge cases** | Empty input, None, unicode | `process("")` → doesn't crash |
| **Error cases** | Invalid input raises correctly | `create_run("dup")` → ValueError |
| **Isolation** | State doesn't leak between tests | Two runs don't share files |
| **Roundtrip** | Save → load returns same data | Record → replay returns same response |
| **Idempotency** | Repeated calls are safe | `start()` twice → RuntimeError |
| **Boundaries** | Limits and caps work | Token budget at 100% → rotation |

### What NOT to Test

- Don't test Python stdlib behavior (json.dumps works)
- Don't test third-party libraries (litellm, chromadb internals)
- Don't make real API calls (mock everything external)
- Don't test private methods directly (test via public interface)
- Don't write tests that depend on execution order

---

## Development Lab

The lab is a 1:1 replica test environment where you can test everything
without touching the live instance. Same AgentLoop code, different seams.

### Architecture

```
Live Ene                              Lab Ene
├─ workspace: ~/.nanobot/workspace    ├─ workspace: ~/.nanobot-lab/runs/{name}/workspace
├─ sessions: ~/.nanobot/sessions      ├─ sessions: ~/.nanobot-lab/runs/{name}/sessions
├─ channels: Discord, Telegram        ├─ channels: MockChannel (scripted injection)
├─ provider: OpenRouter (DeepSeek)    ├─ provider: RecordReplayProvider (cached or free)
└─ cost: real $$                      └─ cost: ~$0
```

Three injection seams, same AgentLoop:
1. **Paths** — `set_data_path()` redirects all state to lab directory
2. **Provider** — `RecordReplayProvider` wraps real provider with cache
3. **Channel** — `MockChannel` injects messages programmatically

### Storage Layout

```
~/.nanobot-lab/
├── _snapshots/              # Named, immutable state snapshots
│   └── {name}/
│       ├── workspace/       # Full copy of workspace
│       ├── sessions/        # Full copy of sessions
│       └── manifest.json    # Metadata (source, file count, size)
├── runs/                    # Active/completed test runs
│   └── {name}/
│       ├── workspace/       # Isolated workspace
│       ├── sessions/        # Isolated sessions
│       └── audit/           # Event trail JSONL
└── cache/                   # Shared LLM response cache
    └── llm_responses/       # Hash → response JSON files
```

### CLI Commands

```bash
# ── Snapshots (named, immutable) ──────────────────────────

# Snapshot live state
nanobot lab snapshot create live_feb19 --from live

# Snapshot a lab run
nanobot lab snapshot create after_trust_test --from run:my_test

# List all snapshots
nanobot lab snapshot list

# Delete a snapshot
nanobot lab snapshot delete old_snapshot

# ── Test Runs ─────────────────────────────────────────────

# Run a JSONL script from a snapshot
nanobot lab run script.jsonl --snapshot live_feb19 --model "deepseek/deepseek-chat-v3-0324:free"

# Run with cached LLM responses ($0 cost, deterministic)
nanobot lab run script.jsonl --snapshot live_feb19 --replay

# Run with recording (caches responses for future --replay)
nanobot lab run script.jsonl --snapshot live_feb19 --record

# Run fresh (empty state, no snapshot)
nanobot lab run script.jsonl --fresh

# Name the run explicitly
nanobot lab run script.jsonl --snapshot live_feb19 --name prompt_v2_test

# List all runs
nanobot lab list

# ── Stress Testing ────────────────────────────────────────

# 10 users, 100 messages each
nanobot lab stress --users 10 --messages 100 --snapshot live_feb19

# ── Analysis ──────────────────────────────────────────────

# Compare two runs
nanobot lab diff run_a run_b

# View audit summary
nanobot lab audit my_run
```

### Script Format (JSONL)

Each line is a JSON message or directive:

```jsonl
{"sender_id": "user_1", "display_name": "Alice", "content": "hey ene!", "chat_id": "lab_general"}
{"sender_id": "user_2", "display_name": "Bob", "content": "anyone here?", "expect_response": false}
{"_delay": 2.0}
{"sender_id": "user_1", "display_name": "Alice", "content": "ene what do you think?"}
```

**Message fields:**
| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `sender_id` | Yes | — | Platform user ID |
| `content` | Yes | — | Message text |
| `display_name` | No | sender_id | Display name |
| `username` | No | sender_id | Username |
| `chat_id` | No | "lab_general" | Channel ID |
| `guild_id` | No | "lab_guild" | Server ID (None for DMs) |
| `is_reply_to_ene` | No | false | Whether replying to Ene |
| `expect_response` | No | true | Wait for Ene's response |

**Special directives (keys starting with `_`):**
| Directive | Description |
|-----------|-------------|
| `_delay` | Pause N seconds before next message |
| `_verify` | State assertion (for stress test verifier) |

### Programmatic Usage

```python
from nanobot.lab.harness import LabHarness, LabConfig

config = LabConfig(
    run_name="my_test",
    snapshot_name="live_feb19",
    model="deepseek/deepseek-chat-v3-0324:free",
    cache_mode="replay_or_live",  # Try cache, fall back to real
)

lab = LabHarness(config=config)
await lab.start()

# Single message
response = await lab.inject("hello ene!", sender_id="user_1")
print(response.content if response else "no response")

# Scripted sequence
results = await lab.run_script([
    {"sender_id": "user_1", "content": "hey ene!"},
    {"sender_id": "user_2", "content": "what's up everyone"},
    {"_delay": 1.0},
    {"sender_id": "user_1", "content": "ene what do you think?"},
])

# Check state after test
state = lab.get_state()
print(state["core_memory"])
print(state["social_profiles"])

# Provider cache stats
print(lab.get_provider_stats())

await lab.stop()
```

### RecordReplayProvider Modes

| Mode | Behavior | Cost | Use Case |
|------|----------|------|----------|
| `record` | Always call real LLM, save response | $$ | Build cache for new scenarios |
| `replay` | Return cached response, error on miss | $0 | CI, regression testing |
| `replay_or_live` | Try cache, fall back to real LLM | ~$0 | Development (cache grows over time) |
| `passthrough` | Always call real LLM, no caching | $$ | Live-like testing |

### State Verification (tau-bench pattern)

Verify actual state, not just text output:

```python
from nanobot.lab.stress import StateVerifier

state = lab.get_state()

# Check memory was written
passed, detail = StateVerifier.verify_memory_contains(state, "people", "Alice")
assert passed, detail

# Check social profile exists
passed, detail = StateVerifier.verify_social_profile_exists(state, display_name="Alice")
assert passed, detail

# Check no duplicate responses
passed, detail = StateVerifier.verify_no_duplicate_responses(results)
assert passed, detail
```

### Audit Trail

Every lab run can capture all LiveTracer events:

```python
from nanobot.lab.audit import AuditCollector

audit = AuditCollector()
audit.attach_to_tracer(lab._agent_loop._live)

# ... run messages ...

audit.save(lab.paths.audit_dir / "run.jsonl")
summary = audit.summary()
# {'total_events': 42, 'classifications': {'RESPOND': 5, 'CONTEXT': 12}, ...}
```

### Comparing Runs (Regression Detection)

```python
from nanobot.lab.diff import AuditDiff

result = AuditDiff.compare(
    Path("runs/before/audit/run.jsonl"),
    Path("runs/after/audit/run.jsonl"),
)
print(result["classification_changes"])  # What got classified differently
print(result["response_diffs"])          # What responses changed
```

---

## Lab Components Reference

| Component | File | Purpose |
|-----------|------|---------|
| MockChannel | `nanobot/channels/mock.py` | Programmatic message injection + response capture |
| RecordReplayProvider | `nanobot/providers/record_replay.py` | Cache LLM responses for $0 replay |
| LabState | `nanobot/lab/state.py` | Snapshots, runs, forks, identity seeding |
| LabHarness | `nanobot/lab/harness.py` | Main orchestrator — wires everything |
| AuditCollector | `nanobot/lab/audit.py` | Captures tracer events for post-analysis |
| AuditDiff | `nanobot/lab/diff.py` | Compares two audit trails |
| StressTest | `nanobot/lab/stress.py` | Multi-user, flood, trust ladder generators |
| StateVerifier | `nanobot/lab/stress.py` | State-based assertions (tau-bench pattern) |

---

## Whitelist Rules (Testing)

| # | Rule | Meaning |
|---|------|---------|
| T1 | All new code must have tests | No untested code ships |
| T2 | Tests mirror source structure | `nanobot/foo/bar.py` → `tests/foo/test_bar.py` |
| T3 | pytest only | No unittest, no nose |
| T4 | Mock external APIs | Tests must work offline |
| T5 | All tests pass before commit | `python -m pytest tests/ -x -q` |
