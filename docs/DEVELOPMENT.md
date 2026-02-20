# Ene Development Guide

How to add new features, fix bugs, and make changes to Ene without
breaking anything. This guide is for Claude Code (the agent that
implements changes) and for Dad (who approves them).

---

## The Golden Rule

**Every change must leave the codebase in a better state than it found it.**

Before writing any code:
1. Read `docs/WHITELIST.md` — does your plan conflict with any decision?
2. Read `CLAUDE.md` — understand architecture invariants
3. Run `python -m pytest tests/ -x -q` — know the baseline (currently ~825 passing)
4. After every change, run tests again — must still be green

---

## Development Workflow

### Adding a New Feature

```
1. UNDERSTAND    → Read relevant docs and code
2. CHECK         → Verify against WHITELIST.md
3. PLAN          → Design the change (use plan mode for non-trivial work)
4. IMPLEMENT     → Write the code
5. TEST          → Write tests alongside the code
6. VERIFY        → Run full test suite
7. LAB (optional)→ Test in the development lab for behavioral changes
8. DOCUMENT      → Update CHANGELOG.md, WHITELIST.md if needed
```

### Step-by-Step for Each Type of Change

#### Adding a New Ene Module

Modules live in `nanobot/ene/` and register via `ModuleRegistry`.

1. **Create the module directory:**
   ```
   nanobot/ene/mymodule/
   ├── __init__.py     # MyModule(EneModule) class
   ├── core.py         # Business logic
   └── tools.py        # Tool implementations (if any)
   ```

2. **Implement `EneModule` base class:**
   ```python
   from nanobot.ene import EneModule, EneContext

   class MyModule(EneModule):
       name = "mymodule"
       description = "What this module does"
       can_modify_self = False
       can_connect_channels = False
       can_access_tools = ["my_tool"]

       async def initialize(self, ctx: EneContext) -> None: ...
       def get_context(self, message) -> str: ...
       def get_tools(self) -> list: ...
   ```

3. **Register in the module registry** (check how existing modules register)

4. **Create matching test directory:**
   ```
   tests/ene/mymodule/
   ├── __init__.py
   ├── test_core.py
   └── test_tools.py
   ```

5. **Write tests that cover:**
   - Module initialization
   - Context generation
   - Tool execution (mock any LLM calls)
   - Edge cases and error handling

6. **Add WHITELIST entry** if this involves an architectural decision

7. **Add CHANGELOG entry**

#### Adding a New Channel Adapter

Channels implement `BaseChannel` and live in `nanobot/channels/`.

1. **Create `nanobot/channels/newchannel.py`**
   - Extend `BaseChannel`
   - Implement `start()`, `stop()`, `send()`
   - Use `_handle_message()` to publish to bus (same path as all channels)

2. **Create `tests/channels/test_newchannel.py`**
   - Test message injection via `_handle_message()`
   - Test `is_allowed()` filtering
   - Test `send()` output format
   - Mock all external APIs (WHITELIST T4)

3. **Add config schema** in `nanobot/config/schema.py`

4. **Add WHITELIST entry** (A4 covers the pattern)

#### Adding a New Tool

Tools register with `ToolRegistry` and live in `nanobot/agent/tools/`.

1. **Create the tool class** implementing the tool interface
2. **Register in the appropriate module** or in `loop.py` for core tools
3. **Test the tool** — mock any external calls
4. **Check security implications:**
   - Should this be in `RESTRICTED_TOOLS`? (Dad-only)
   - Does it need rate limiting?
   - Can it modify state? (needs careful testing)

#### Modifying Core Systems (REQUIRES APPROVAL)

Core systems are LOCKED (WHITELIST A3):
- `nanobot/agent/loop.py` — agent loop
- `nanobot/agent/security.py` — security, DAD_IDS
- `nanobot/agent/response_cleaning.py` — output sanitization
- `nanobot/session/` — session storage

**Before touching these:**
1. Get explicit approval from Dad
2. Understand why the current behavior exists (read WHITELIST, ARCHITECTURE)
3. Write tests FIRST (test the current behavior, then test your change)
4. Run full suite before AND after
5. Test in the lab with realistic scenarios

#### Fixing a Bug

1. **Reproduce the bug** — write a failing test first
2. **Understand the root cause** — don't just fix the symptom
3. **Fix the code** — minimal change, don't refactor while fixing
4. **Verify the test passes** — the test you wrote in step 1
5. **Run full suite** — make sure you didn't break anything else
6. **Add to CHANGELOG** — document what was wrong and how it's fixed
7. **Check common bug table** in CLAUDE.md — is this a known pattern?

---

## Testing Requirements for Every Change

### Mandatory

Every change MUST include:

- [ ] **Unit tests** for new code (`tests/` mirroring `nanobot/` structure)
- [ ] **Full suite passes** (`python -m pytest tests/ -x -q`)
- [ ] **No regressions** (same or more tests than before)

### Required for Behavioral Changes

Changes that affect how Ene responds, classifies, or processes messages:

- [ ] **Lab test** with realistic message script
- [ ] **State verification** — check memory, trust, threads after test
- [ ] **Regression comparison** — diff with previous behavior if possible

### Required for Core System Changes

Changes to loop.py, security.py, session, response_cleaning:

- [ ] **Explicit Dad approval**
- [ ] **Test the current behavior first** (prove your test catches the change)
- [ ] **Lab test with multi-user script** (stress the pipeline)
- [ ] **Verify invariants** from CLAUDE.md still hold

---

## Using the Lab for Development

### When to Use the Lab

Use the lab when your change affects:
- How messages get classified (RESPOND / CONTEXT / DROP)
- How Ene responds (prompt changes, tool changes)
- Memory operations (save, edit, delete, consolidation)
- Social/trust calculations
- Thread detection or formatting
- Daemon behavior
- Multi-message handling (debounce, merging)

### Lab Testing Workflow

```bash
# 1. Snapshot current live state
nanobot lab snapshot create before_change --from live

# 2. Make your code change
# ... edit files ...

# 3. Run unit tests
python -m pytest tests/ -x -q

# 4. Write a test script (or use a generator)
# script.jsonl:
# {"sender_id": "user_1", "display_name": "Alice", "content": "hey ene!"}
# {"sender_id": "user_1", "display_name": "Alice", "content": "ene what do you think about cats?"}

# 5. Run the script in the lab
nanobot lab run script.jsonl --snapshot before_change --name before_test

# 6. Run the same script after your change
nanobot lab run script.jsonl --snapshot before_change --name after_test

# 7. Compare the two runs
nanobot lab diff before_test after_test
```

### Programmatic Lab Testing (in test files)

```python
@pytest.mark.asyncio
async def test_my_behavioral_change(tmp_path):
    """Verify the new behavior works as expected."""
    from nanobot.lab.harness import LabHarness, LabConfig
    from nanobot.lab.state import set_lab_root

    set_lab_root(tmp_path)

    lab = LabHarness(
        run_name="behavior_test",
        model="fake/model",
        cache_mode="passthrough",
        provider=FakeProvider(),
    )
    await lab.start()
    try:
        response = await lab.inject("hey ene!", sender_id="user_1")
        # Assert on the response or state
        state = lab.get_state()
        # ...
    finally:
        await lab.stop()
        set_lab_root(None)
```

---

## Non-Regression Checklist

Before considering any change complete, verify:

### Architecture Invariants (from CLAUDE.md)

- [ ] `DAD_IDS` in security.py is still hardcoded, not from config/env
- [ ] `mark_ene_responded()` is called after every successful response
- [ ] `condense_for_session()` strips thread context before session storage
- [ ] Session only stores turns where Ene actually did something
- [ ] `last_shown_index` on threads prevents re-replay
- [ ] All LLM output goes through `clean_response()` before Discord
- [ ] Daemon prompt does not contain Dad's raw platform IDs
- [ ] `message` tool terminates the agent loop (max 1 response per batch)

### Test Health

- [ ] `python -m pytest tests/ -x -q` — all pass
- [ ] Test count same or higher than before your change
- [ ] No new warnings (check pytest output)
- [ ] No flaky tests (run twice if in doubt)

### Documentation

- [ ] `docs/CHANGELOG.md` entry for every change
- [ ] `docs/WHITELIST.md` entry if new architectural decision
- [ ] Code comments explain WHY, not WHAT (WHITELIST D4)
- [ ] Updated CLAUDE.md if public interface changed

---

## Adding Lab Infrastructure

When adding new lab components (new generators, verifiers, audit features):

1. **New file goes in `nanobot/lab/`**
2. **Test file goes in `tests/lab/`**
3. **Use `set_lab_root(tmp_path)` in test fixtures** for isolation
4. **Use `FakeProvider` in tests** — never call real LLMs in unit tests
5. **Document in `docs/TESTING.md`** under the appropriate section
6. **Add CLI command** in `nanobot/cli/commands.py` under `lab_app` if user-facing

### Adding a New Stress Generator

```python
# In nanobot/lab/stress.py
class StressTest:
    @staticmethod
    def generate_my_scenario(...) -> list[dict[str, Any]]:
        """Describe what this scenario tests."""
        messages = []
        # ... build message sequence ...
        return messages
```

```python
# In tests/lab/test_stress.py
def test_my_scenario_generates_correct_messages():
    script = StressTest.generate_my_scenario(...)
    assert len(script) == expected_count
    # Verify structure, metadata, etc.
```

### Adding a New State Verifier

```python
# In nanobot/lab/stress.py
class StateVerifier:
    @staticmethod
    def verify_my_condition(state, ...) -> tuple[bool, str]:
        """Check specific state condition.
        Returns (passed, detail) for clear reporting."""
        # ... check state ...
        return passed, detail
```

---

## File Ownership and Lock Status

| File / Directory | Status | Who Can Modify |
|------------------|--------|----------------|
| `nanobot/agent/loop.py` | **LOCKED** | Dad + Claude Code (with approval) |
| `nanobot/agent/security.py` | **LOCKED** | Dad + Claude Code (with approval) |
| `nanobot/agent/response_cleaning.py` | **LOCKED** | Dad + Claude Code (with approval) |
| `nanobot/session/` | **LOCKED** | Dad + Claude Code (with approval) |
| `nanobot/ene/` | Open | Claude Code (tests required) |
| `nanobot/channels/` | Open | Claude Code (tests required) |
| `nanobot/providers/` | Open | Claude Code (tests required) |
| `nanobot/lab/` | Open | Claude Code (tests required) |
| `nanobot/cli/` | Open | Claude Code (tests required) |
| `~/.nanobot/workspace/SOUL.md` | **IDENTITY** | Dad only |
| `~/.nanobot/workspace/AGENTS.md` | **IDENTITY** | Dad only (sync with loop.py) |
| `docs/WHITELIST.md` | **APPEND-ONLY** | Dad + Claude Code |

---

## Common Patterns

### Mocking the LLM Provider

```python
class FakeProvider(LLMProvider):
    def __init__(self, response=None):
        super().__init__()
        self._response = response or LLMResponse(content="fake")
        self.call_count = 0
        self.last_messages = None

    async def chat(self, messages, tools=None, model=None, **kwargs):
        self.call_count += 1
        self.last_messages = messages
        return self._response

    def get_default_model(self) -> str:
        return "fake/model"
```

### Isolating State in Tests

```python
from nanobot.utils.helpers import set_data_path
from nanobot.lab.state import set_lab_root

@pytest.fixture(autouse=True)
def isolated(tmp_path):
    """Redirect all global state to tmp_path."""
    set_data_path(tmp_path / "data")
    set_lab_root(tmp_path / "lab")
    yield
    set_data_path(None)
    set_lab_root(None)
```

### Testing File I/O

```python
def test_save_and_load(tmp_path):
    path = tmp_path / "test.json"
    save_data(path, {"key": "value"})
    loaded = load_data(path)
    assert loaded["key"] == "value"
```

### Testing Async Code

```python
@pytest.mark.asyncio
async def test_async_with_timeout():
    result = await asyncio.wait_for(
        some_async_function(),
        timeout=5.0
    )
    assert result is not None
```

### Testing Error Handling

```python
def test_invalid_input_raises():
    with pytest.raises(ValueError, match="already exists"):
        create_duplicate("name")

def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        load_from("nonexistent")
```

---

## Debugging

### Using the Live Dashboard

```bash
# Start Ene normally
python -m nanobot

# Open in browser
http://localhost:18791/live
```

The dashboard shows every step of message processing in real-time:
message arrival → debounce → daemon classification → LLM call → tool execution → response.

### Using Debug Traces

Debug traces write per-message JSON logs to `~/.nanobot/workspace/memory/logs/debug/`.
Each file contains the full context that was sent to the LLM.

### Lab Audit Trail

For lab runs, the audit collector captures every tracer event:

```python
audit = AuditCollector()
audit.attach_to_tracer(lab._agent_loop._live)
# ... run messages ...
print(audit.summary())
# Inspect specific events
for e in audit.get_events("classification"):
    print(e)
```

---

## Summary: The Agent's (Claude Code's) Responsibility

When asked to make a change:

1. **Read first.** Understand the existing code, docs, and WHITELIST before writing anything.
2. **Test always.** Write tests for every new file. Run the full suite.
3. **Isolate state.** Use `tmp_path`, `set_data_path()`, `set_lab_root()` — never touch live paths in tests.
4. **Mock externals.** No real API calls in tests. Use FakeProvider, MockChannel.
5. **Document decisions.** CHANGELOG for every change, WHITELIST for architecture decisions.
6. **Preserve invariants.** Check the non-regression checklist above.
7. **Lab test behavior.** If the change affects how Ene processes messages, test it in the lab.
8. **Leave it better.** Every change should leave the codebase cleaner than you found it.
