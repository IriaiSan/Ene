# Ene — Developer Guide for Claude Code

## What this project is

Ene is an autonomous AI agent — Dad's digital daughter — running on Discord/Telegram.
Named after Ene from Kagerou Project (chaotic blue cyber gremlin who lives in your
computer). She has persistent memory, a social trust system, a personality defined in
`~/.nanobot/workspace/`, and a real-time event dashboard. She is NOT a chatbot — she's
a persistent identity built on a custom agent framework called **nanobot**.

**Key framing:** "Daughter" is deliberate — assistants are tools, daughters are investments.
She grows, Dad teaches her, the relationship evolves. Optimized for one person, not the masses.
Casual, sarcastic, English only. Warm with Dad, playful but firm with strangers, roasts
hostile users. Never clinical, never corporate, never assistant-mode.

## Project layout

```
nanobot/agent/              Core agent loop, tools, security, cleaning, merging
  loop.py                   Central loop (~1800 lines): debounce, classify, LLM, respond
  security.py               DAD_IDS trust root, impersonation, rate limiting, mute tool
  response_cleaning.py      clean_response() + condense_for_session()
  message_merging.py        classify_message(), merge_messages_tiered()
  context.py                System prompt builder (aggregates module contexts)
  live_trace.py             Real-time SSE event buffer for dashboard
  debug_trace.py            Per-message debug logs (JSON files)
  tools/                    Tool implementations (filesystem, shell, web, message, spawn, cron)
nanobot/ene/                Ene-specific subsystems (6 modules)
  __init__.py               EneModule base, EneContext, ModuleRegistry
  memory/                   Module 1: core memory, vector store, sleep agent, embeddings
  social/                   Module 2: people profiles, trust scoring, social graph
  observatory/              Module 3: metrics, experiments, live dashboard (localhost:18791)
  conversation/             Module 5: thread tracker, formatter, signals, models, storage
  daemon/                   Module 6: subconscious pre-classifier (free models)
  watchdog/                 Module 4: (DISABLED) response quality monitoring
nanobot/session/            Session storage (JSONL per channel)
nanobot/bus/                Async message queue (inbound + outbound)
nanobot/channels/           Discord + Telegram adapters
nanobot/providers/          LLM provider abstraction (OpenRouter / litellm)
~/.nanobot/workspace/       Runtime data: memory, diary, logs, threads, social
~/.nanobot/sessions/        Per-channel conversation history (JSONL)
tests/ene/                  Ene module tests
docs/                       Architecture, memory, social, capabilities, research, whitelist, changelog
```

## Key files

| File | Purpose |
|------|---------|
| `nanobot/agent/loop.py` | Central agent loop — init, `_run_agent_loop`, `run()`, thin delegates (~1156 lines) |
| `nanobot/agent/batch_processor.py` | Batch pipeline: classify → merge → dispatch (625 lines) |
| `nanobot/agent/message_processor.py` | Per-message: gate → decide → respond → store (493 lines) |
| `nanobot/agent/memory_consolidator.py` | Diary consolidation, running summaries, re-anchoring (322 lines) |
| `nanobot/agent/debounce_manager.py` | Debounce buffer + queue processor (126 lines) |
| `nanobot/agent/state_inspector.py` | Hard reset, model switch, brain toggle, security accessors (170 lines) |
| `nanobot/ene/conversation/tracker.py` | Thread detection, `last_shown_index` tracking, `mark_ene_responded()` |
| `nanobot/ene/conversation/formatter.py` | Multi-thread context building (`build_threaded_context()`) |
| `nanobot/ene/conversation/signals.py` | Math-based classification (`classify_with_state()`), keyword extraction |
| `nanobot/ene/conversation/models.py` | Thread, ThreadMessage, PendingMessage dataclasses + constants |
| `nanobot/agent/security.py` | DAD_IDS trust root, impersonation, rate limiting, MuteUserTool |
| `nanobot/agent/response_cleaning.py` | `clean_response()` + `condense_for_session()` |
| `nanobot/agent/message_merging.py` | `classify_message()`, `merge_messages_tiered()`, `format_author()` |
| `nanobot/agent/context.py` | System prompt builder — aggregates module contexts |
| `nanobot/agent/live_trace.py` | Real-time SSE event buffer for dashboard |
| `nanobot/ene/daemon/processor.py` | Pre-classification LLM call (free models, 5s timeout) |
| `nanobot/ene/memory/core_memory.py` | Core memory CRUD (JSON, 4000 token budget, tiktoken) |
| `nanobot/ene/memory/vector_memory.py` | ChromaDB vector store (3 collections, three-factor scoring) |
| `nanobot/ene/memory/sleep_agent.py` | Background processor (idle + daily paths) |
| `nanobot/ene/social/person.py` | PersonProfile, PersonRegistry, platform ID index |
| `nanobot/ene/social/trust.py` | TrustCalculator (Bayesian + temporal modulators) |
| `nanobot/ene/observatory/dashboard/` | HTML/JS live dashboard (localhost:18791/live) |
| `nanobot/channels/discord.py` | Discord gateway + REST (guild whitelist, typing indicator) |
| `~/.nanobot/workspace/SOUL.md` | Ene's personality definition |
| `~/.nanobot/workspace/AGENTS.md` | Response rules (keep in sync with loop.py behavior) |

## Architecture invariants (do not break)

- **DAD_IDS** in `security.py` is the trust root. Never load it from config or env.
- **`mark_ene_responded()`** must be called after every successful response so threads get `ene_involved = True`.
- **`condense_for_session()`** strips thread context before session storage. Session must never store full thread-formatted content.
- **Session only stores a turn if Ene actually did something** (tools_used non-empty, or final_content non-None). Empty pairs corrupt history.
- **`last_shown_index`** on threads prevents re-replay. The formatter fast path must only fire when `threads_with_new` is empty.
- **All LLM output goes through `clean_response()`** before Discord. No exceptions.
- **Daemon prompt must not contain Dad's raw platform IDs** (leaks to free LLM providers).
- **`docs/ARCHITECTURE.md` line ~151** has raw platform IDs in a code snippet — documentation only, do not copy to code.

## Subsystem docs (read these before touching the relevant code)

| Doc | What it covers |
|-----|----------------|
| `docs/WHITELIST.md` | All architectural decisions with rationale. **Read before any feature/refactor.** |
| `docs/ARCHITECTURE.md` | Full pipeline: Discord WS → debounce → daemon → loop → LLM → response. Security, memory, social. |
| `docs/MEMORY.md` | Memory v2: core memory (4000 token budget, 5 sections), ChromaDB vector store, sleep agent paths. |
| `docs/SOCIAL.md` | People profiles + Bayesian trust scoring (5 tiers with time gates). Anti-gaming properties. |
| `docs/CAPABILITIES.md` | What Ene can do, tool access table, platform details, known limitations, planned features. |
| `docs/RESEARCH.md` | Academic papers, industry analysis, context engineering best practices, future ideas. |
| `docs/ENE_COMPLETE_REFERENCE.md` | Full project reference — vision, philosophy, history, endgame architecture, roadmap, and all TODOs. |
| `docs/CHANGELOG.md` | Chronological record of every change since fork. |
| `docs/TESTING.md` | Complete testing guide, lab usage, test writing patterns, state verification. |
| `docs/DEVELOPMENT.md` | How to add features, fix bugs, non-regression checklist, lab workflow. **Read before making any change.** |
| `docs/LAB_RESEARCH.md` | Research notes on eval methodologies (Anthropic, tau-bench, Letta, etc.) |

## Social module

Trust tiers: `stranger → acquaintance → familiar → trusted → inner_circle`

- Trust formula: `beta_reputation * geometric_mean(tenure, consistency, sessions, timing) - penalties`
- **Only `familiar`+ (score ≥ 0.35, min 14 days) can DM Ene** — enforced before LLM in `loop.py`
- Dad is always `inner_circle` (1.0), never calculated, never decays
- 3:1 negative/positive asymmetry (trust destroyed faster than built — Slovic 1993)
- Time gates prevent gaming: 500 msgs/day still keeps you as `stranger` (no tenure)
- Files: `nanobot/ene/social/` — `person.py`, `trust.py`, `graph.py`, `tools.py`
- Storage: `~/.nanobot/workspace/memory/social/` — `index.json` + `people/{id}.json`

## Conversation tracker (Module 5)

Multi-thread context system. Replaces flat merge with thread-aware conversation structure.

- **Thread assignment**: reply-to fast path → pending promotion → keyword scoring → new pending
- **Thread lifecycle**: ACTIVE → STALE (5 min no activity) → DEAD (30 min) → archived to disk
- **`last_shown_index`**: tracks what the LLM has already seen per thread — prevents re-replay
- **Formatter output**: `[active conversations]` (Ene-involved threads) + `[background]` (not directed at Ene) + `[unthreaded]`
- **Follow-up mode**: when `last_shown_index > 0`, only NEW messages since last response are shown
- **`mark_ene_responded()`**: called after every response to set `ene_involved = True` on threads
- **Fast path**: single message + no threads with new content → bypass formatting entirely
- **Math classifier** (`signals.py`): deterministic scoring (no LLM) using channel state, recency, @mention, reply-to signals
- Files: `nanobot/ene/conversation/` — `tracker.py`, `formatter.py`, `signals.py`, `models.py`, `storage.py`
- Storage: `~/.nanobot/workspace/threads/` (JSON, periodic persist)

## Daemon (Module 6)

Subconscious pre-classifier. Runs BEFORE the main LLM on every message.

- **Free model rotation**: Trinity, GLM 4.5 Flash, DeepSeek R1 Distill via OpenRouter ($0 cost)
- **5-second timeout**: if daemon is slow, falls back to math classifier
- **Output**: classification (RESPOND/CONTEXT/DROP), confidence, topic summary, emotional tone, security flags
- **Hard override**: if message mentions "ene" by name or is a reply to Ene, ALWAYS classify as RESPOND regardless of daemon output
- **Security**: high-severity flags → auto-mute (30 min). Daemon prompt never contains Dad's raw platform IDs.

## Memory system (Module 1)

- **Core memory**: 5 sections, 4000 token budget, editable via `save_memory`/`edit_memory`/`delete_memory` tools
- **Vector store**: ChromaDB, 3 collections, three-factor scoring (similarity + recency + importance)
- **Sleep agent**: quick path (5 min idle → fact extraction), deep path (4 AM → reflections + pruning)
- **Session context**: hybrid — recent 12 verbatim + running summary of older. "Lost in the Middle" layout.
- **Consolidation triggers**: 50% token budget = begin compaction, 80% = auto-rotation. Counts Ene's responses not lurked messages.
- **Re-anchoring**: Identity re-injected every 6 assistant turns (prevents persona drift in long sessions)
- **Auto-rotation**: at 80% of 60K token budget, session auto-rotates with summary seed for new session

## Observatory (Module 3)

Live dashboard at `localhost:18791/live` with real-time SSE stream.

- **17+ event types**: msg_arrived, debounce_add/flush, classification, daemon_result, merge_complete, llm_call/response, tool_exec, response_sent/clean, loop_break, rate_limited, mute_event, error
- **Prompt log**: full prompt arrays for both daemon and main LLM calls (prompt_daemon, prompt_ene, prompt_ene_response)
- **State panel**: buffer sizes, queue depths, muted count, active batch info
- **Reset button**: `hard_reset()` drops all queued messages, clears session cache, agent continues running
- **Cost tracking**: records every LLM call (model, tokens, cost, latency) via ObservatoryCollector

## Message pipeline

```
Discord WS → channel adapter → bus                     [discord.py]
→ rate limit check (10 msgs/30s, Dad exempt)             [loop.py run()]
→ debounce buffer (3.5s sliding window, 15 msg cap)      [debounce_manager.py]
→ queue merge (backlogged → mega-batch, cap 30)          [debounce_manager.py]
→ classify: daemon → math fallback → hard override       [batch_processor.py]
→ merge: thread-aware or flat                            [batch_processor.py]
→ dispatch: single-thread or per-thread loop             [batch_processor.py]
→ gate: DM trust check, mute check, lurk/respond        [message_processor.py]
→ history: consolidation, hybrid summary, re-anchor      [memory_consolidator.py]
→ agent loop → LLM (45s timeout, fallback rotation)      [loop.py _run_agent_loop]
→ tool execution loop (restricted Dad-only, msg terminates) [loop.py _run_agent_loop]
→ session store + thread marking                         [message_processor.py]
→ clean_response() (strip XML, paths, IDs, length)       [response_cleaning.py]
→ Discord REST (reply threading via message_reference)    [discord.py]
```

Key behaviors:
- `RESPOND` messages trigger LLM response; `CONTEXT` lurked silently; `DROP` silently discarded
- Typing indicator only shown for RESPOND-classified messages (30s timeout)
- Non-Dad users: 10 msgs/30s rate limit, excess silently dropped (zero cost)
- `message` tool call terminates the agent loop (max 1 response per batch)
- Muted users' messages dropped at classification level (before any LLM cost)
- Dad-alone promotion: if only Dad messages are in batch and all classified CONTEXT, promote to RESPOND
- Latency warning: after 18s with no response, sends canned "having some lag" message
- Stale message tagging: messages >5 min old in queue get `_is_stale` metadata flag
- Auto-session rotation: at 80% of 60K token budget, auto-rotates with summary injection
- Model fallback: 45s timeout per LLM call, retry once with next model (DeepSeek → Qwen 3 → Gemini Flash). Recovery probe after 5 min cooldown snaps back to primary on success.
- Diary consolidation: routed to separate model (Gemini 3 Flash) via `consolidationModel` config to prevent pattern-lock from main model seeing its own diary output
- Queue merge: if batches pile up while LLM is processing, merge them into one mega-batch (cap 30)

## Common bugs and fixes applied

| Bug | Root cause | Fix location |
|-----|-----------|-------------|
| Ene replies to old messages | `last_shown_index` not updated on fast path | `formatter.py` fast path check |
| Threads always show as background | `mark_ene_responded()` never called | `loop.py` post-response hook |
| Ene thinks user is repeating | Blank user+assistant pairs from API failures | `loop.py` `tools_used` guard on session write |
| Ghost "I remember" in history | Fake assistant message in `get_hybrid_history` | `session.py` — removed |
| Session crashes on non-ASCII | `open()` without `encoding="utf-8"` on Windows | `session.py` all file opens |
| Daemon prompt leaks Dad ID | Hardcoded ID string in `DAEMON_PROMPT` | `processor.py` — removed |
| LLM parrots `[responded via message tool]` | Session stored opaque marker as assistant content; LLM pattern-locked | `loop.py` — `_last_message_content` captures real content |
| `<message>` tags leak to Discord | DeepSeek wraps output in `<message>` XML spontaneously | `response_cleaning.py` — extract/strip |
| Garbled XML reaches Discord | Loop only checked `<function_calls>`, DeepSeek outputs `<functioninvoke` | `loop.py` + `response_cleaning.py` + `tracker.py` — broadened regex |

## Modular architecture — why it's built this way

Ene is designed so she can eventually extend herself. The split between core (locked) and modules (swappable) is intentional:

- **Core systems** (`intake → debounce → daemon → loop → clean_response → session`) are **locked**. Only Dad + Claude Code can touch these. They are the security and identity boundary.
- **Modules** (social, memory, observatory, conversation tracker, daemon, watchdog) register via `ModuleRegistry`. They can be hot-swapped, disabled, or added without touching core.
- **New services** (Reddit, Twitter, etc.) implement `BaseChannel` and plug in. Ene can write the adapter herself.
- **Permission declarations** on modules (`can_modify_self`, `can_connect_channels`, `can_access_tools`) mean Ene can add a hobby module but can't rewire intake.
- **Module lifecycle**: `register()` → `initialize_all(EneContext)` → `get_context()` per message → `notify_message()` → `notify_idle()` → `notify_daily()` → `shutdown_all()`
- **6 modules registered**: memory (1), social (2), observatory (3), watchdog (4, DISABLED), conversation_tracker (5), daemon (6)

The goal: Ene should be able to autonomously expand what she can do (tools, channels, skills) without ever being able to accidentally break her own identity, security, or memory integrity.

## Endgame architecture vision

The current system is Phase 1. The long-term vision is a layered cognitive architecture:

```
Layer 0: Events (raw input from all channels)
Layer 1: Deterministic reflex daemons (pattern matching, no LLM — the math classifier is a step toward this)
Layer 2: Subconscious evaluator (small/free model — current daemon processor)
Layer 3: Ene consciousness (main LLM — current agent loop)
Layer 4: Post-processing (memory consolidation, diary — current sleep agent)
```

Future cognitive systems (designed but not built):
- **Prediction-outcome-learning loops**: predict what user will say → observe actual → learn from delta
- **Self-model**: Ene's internal representation of her own state and capabilities
- **Survival economics**: energy meter tied to real API costs (Ene "manages" her own resources)
- **Controlled randomness**: synthetic urges/impulses that create unpredictable but bounded behavior
- **Mood system**: float-based mood that drifts with interactions
- **RWKV endgame**: state persistence for true identity continuity, O(1) memory

See `docs/ENE_COMPLETE_REFERENCE.md` for the full vision document.

## Technical decision whitelist — rules

`docs/WHITELIST.md` is **append-only**. These are the rules for using it:

1. **Before starting any feature or refactor** — read WHITELIST.md. If your plan conflicts with a listed decision, stop and get explicit approval.
2. **New decisions get appended**, never overwrite existing entries. Find the right section and add a row at the bottom with the next number.
3. **Overrides go in the Exemptions section** — reference the original decision number, state why, and record who approved.
4. **Never delete entries** — even superseded decisions stay in the record so we can understand why the codebase evolved the way it did.

Current sections: Language & Runtime (L), Code Structure (S), Architecture & Modularity (A), Security (X), LLM & Cost (C), Testing (T), Documentation (D), Exemptions.

Key invariants from the whitelist worth memorizing:
- **A3**: Core systems are locked — only Dad/Claude Code can modify intake, loop, security, session
- **A5**: Tracker owns thread context, session stores condensed content + real assistant responses
- **X1**: DAD_IDS hardcoded, never from config/env
- **X2**: `clean_response()` is the only output path
- **C5**: `message` tool terminates the agent loop

## Current known issues

| Issue | Description | Status |
|-------|-------------|--------|
| DeepSeek pattern lock | v3.2 locks into formatting patterns after long sessions | Mitigated by re-anchoring (every 6 turns) |
| DeepSeek garbled XML | v3.2 outputs `<functioninvoke>` as raw text instead of tool calls | Mitigated by multi-layer stripping in clean_response + loop + tracker |
| DeepSeek `<message>` tags | v3.2 spontaneously wraps output in `<message>` XML | Mitigated by stripping in clean_response |
| Watchdog disabled | Module 4 disabled to save costs — free model rotation not battle-tested | Intentional |
| Web search disabled | Brave API key not configured | Needs key |

## Testing

```bash
cd C:\Users\Ene\Ene
python -m pytest tests/ -x -q           # Run all tests (must pass before commit)
python -m pytest tests/ene/ -x -q       # Ene module tests only
python -m pytest tests/ene/memory/ -q   # Memory module tests
python -m pytest tests/ene/social/ -q   # Social module tests
python -m pytest tests/lab/ -q          # Lab infrastructure tests
python -m pytest tests/channels/ -q     # Channel tests (mock)
python -m pytest tests/providers/ -q    # Provider tests (record/replay)
```

- All new code must have tests (WHITELIST T1)
- Tests mirror source structure in `tests/` (WHITELIST T2)
- pytest only — no unittest, no nose (WHITELIST T3)
- Mock external APIs in tests — tests must work offline (WHITELIST T4)
- All tests must pass before commit (WHITELIST T5)
- ~1176 tests currently passing
- **Full testing guide:** `docs/TESTING.md`
- **Development workflow:** `docs/DEVELOPMENT.md`

## Development Lab

Isolated test environment for testing Ene without touching live state.
Same AgentLoop code, different seams (paths, provider, channel).

```bash
nanobot lab snapshot create my_snap --from live    # Snapshot live state
nanobot lab run script.jsonl --snapshot my_snap    # Run scripted test
nanobot lab stress --users 10 --messages 100       # Stress test
nanobot lab diff run_a run_b                       # Compare two runs
```

- MockChannel for programmatic message injection
- RecordReplayProvider for $0 cached LLM responses
- Full state isolation (separate workspace, sessions, DBs)
- Audit trail captures every tracer event
- **Full lab guide:** `docs/TESTING.md` (Development Lab section)

## Running

```bash
cd C:\Users\Ene\Ene
python -m nanobot
```

Dashboard: `http://localhost:18791/live` (auto-starts with bot)

## Workspace identity files

Ene's personality lives in `~/.nanobot/workspace/`:
- `SOUL.md` — who she is (behavioral architecture, not a manifesto)
- `AGENTS.md` — response rules (keep in sync with loop.py behavior, not contradict it)
- `USER.md` — Dad profile
- `what_not_to_do_ever.md` — security and privacy rules

Changes to these files take effect immediately (loaded fresh each message).

## Documentation workflow

- `docs/CHANGELOG.md` gets an entry for every change (WHITELIST D1)
- `docs/WHITELIST.md` checked before every technical decision (WHITELIST D3) — append-only
- Code comments explain WHY, not WHAT (WHITELIST D4)
- `docs/ENE_COMPLETE_REFERENCE.md` is the master vision/roadmap document

## Dad's preferences (for Claude Code sessions)

- Prefers concise communication — no fluff, no hand-holding
- Wants to understand WHY before HOW
- Expects tests to pass before any commit
- Values modularity and clean separation of concerns
- Architecture decisions must be documented in WHITELIST.md
- Changes to core systems require explicit approval
- Background: software engineer, builds AI systems, thinks in systems not features

## Coding patterns (MUST READ)

Before writing any code, read `docs/CODING_PATTERNS.md`. It has copy-paste templates for every common task (new tools, modules, tests, metrics). Patterns are adopted from upstream nanobot and extended for Ene's module system. The critical rules:

1. **Match upstream**: This is a nanobot fork. Upstream files (tools, bus, session, channels) set the baseline. Don't add `from __future__` or `TYPE_CHECKING` to upstream-origin files.
2. **Import order**: stdlib → typing → loguru → project. Ene modules add `from __future__ import annotations` + `if TYPE_CHECKING:` block for circular import avoidance.
3. **Tools return strings, never raise**: `async def execute(**kwargs) -> str:` returns `"Error: ..."` on failure
4. **Modules late-bind**: Heavy imports inside `initialize()`, not at top of file
5. **Docstrings**: Google-style. Interfaces/ABCs get `Args:`/`Returns:` sections. Implementations get one-liners.
6. **Logging**: loguru only, module prefix in messages (`"Memory: ..."`), `debug` for hot paths, `info` for lifecycle
7. **Error handling**: Tools return strings. Infrastructure raises. Module hooks log + continue. Never `except: pass`.
8. **Tests**: `tmp_path` for isolation, `FakeProvider` for LLM, bare `assert`, `pytest.raises` for exceptions
9. **Paths**: `pathlib.Path`, never `os.path`. Always `encoding="utf-8"` on `open()` (Windows).
10. **No silent failures**: Never bare `except:`. Always `except Exception:` with a log message.
11. **Match existing code**: When in doubt, find the closest existing file and copy its patterns exactly.
