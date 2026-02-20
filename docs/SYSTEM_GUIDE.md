# Ene — System Guide

> Start here. This is the one document that explains the whole system.
> Everything else is a deep dive — this is the map.

---

## What Is This?

Ene is an AI agent that lives on Discord and Telegram. She has persistent memory, tracks relationships with people, builds trust over time, and has her own personality. Built on a fork of [nanobot](https://github.com/HKUDS/nanobot), optimized for one person (Dad).

- **Stack:** Python 3.11, DeepSeek v3.2 via OpenRouter, Discord WebSocket, ChromaDB, SQLite
- **Start:** `python -m nanobot` from `C:\Users\Ene\Ene`
- **Dashboard:** `http://localhost:18791/live` (starts automatically)
- **Tests:** `python -m pytest tests/ -x -q` (~937 passing)

---

## How a Message Becomes a Response

```
Discord message arrives
  |
  v
[Rate limit] ---- non-Dad: 10 msgs/30s, excess silently dropped
  |
  v
[Debounce buffer] ---- 3.5s sliding window, batches up to 15 messages
  |
  v
[Daemon] ---- free LLM classifies each message:
  |              RESPOND = needs a reply
  |              CONTEXT = just lurk (store, don't reply)
  |              DROP = muted/spam/irrelevant
  |            5s timeout -> falls back to math classifier
  |            Hard override: @mention or reply-to-Ene = always RESPOND
  |
  v
[Conversation tracker] ---- assigns messages to threads
  |                          builds context: [active] + [background] + [unthreaded]
  |                          tracks what LLM has already seen (no replay)
  |
  v
[Main LLM] ---- DeepSeek v3.2 (45s timeout, fallback: Qwen 3 -> Gemini Flash)
  |               system prompt = identity + workspace files + module contexts + history
  |               tool loop: up to 20 iterations, message tool terminates loop
  |
  v
[clean_response()] ---- strips reflection blocks, paths, IDs, tool XML
  |                      enforces length (500 chars public, 1900 hard limit)
  |                      blocks non-English, strips assistant-mode endings
  |
  v
Discord reply sent (threaded via message_reference)
```

**The key insight:** Messages batch up, get classified by a free model ($0), then only RESPOND messages hit the paid LLM. CONTEXT messages are lurked. DROP messages cost nothing.

**Running in parallel:** Observatory tracks all events + costs. Memory consolidates during idle time. Social updates trust after every interaction.

---

## Where Everything Lives

### Code (`C:\Users\Ene\Ene\nanobot\`)

```
The Pipeline (message in -> response out):
  agent/loop.py              Main loop. Debounce, classify, LLM, tools, send. (~2100 lines)
  agent/context.py           Builds the system prompt from all sources
  agent/security.py          DAD_IDS, rate limiting, muting, impersonation checks
  agent/response_cleaning.py clean_response() — sole output sanitization path
  agent/message_merging.py   Merges batched messages into LLM-ready format
  agent/prompts/             Prompt templates (.txt files) + PromptLoader

Ene's Brain (6 pluggable modules):
  ene/memory/                Core memory + vector store + sleep agent
  ene/social/                People profiles + trust scoring
  ene/conversation/          Thread tracking + multi-thread context formatting
  ene/daemon/                Free LLM pre-classifier
  ene/observatory/           Metrics, dashboard, health monitoring
  ene/watchdog/              (DISABLED) Response quality auditing

Connections:
  channels/discord.py        Discord gateway WebSocket + REST API
  bus/queue.py               Async message queue (inbound/outbound)
  providers/litellm_provider.py  LLM abstraction (OpenRouter, model fallback)
  session/manager.py         Conversation history (JSONL per channel)

Config:
  config/schema.py           Pydantic config schema
```

### Runtime Data (`~/.nanobot/`)

```
config.json                  API keys, model settings, channel tokens (NOT in git)

workspace/
  SOUL.md                    Who Ene is (personality, loaded every message)
  AGENTS.md                  Response rules (keep in sync with loop.py)
  USER.md                    Dad's profile
  what_not_to_do_ever.md     Security/privacy hard rules
  memory/
    core.json                Structured memory (5 sections, 4000 token budget)
    diary/YYYY-MM-DD.md      Daily diary entries
    social/                  People profiles + trust data
      index.json             Platform ID -> person ID (fast lookup)
      people/{id}.json       One file per known person
    threads/                 Active thread state (JSON)
  chroma_db/                 Vector memory (ChromaDB, 3 collections)

sessions/                    Conversation JSONL files (one per channel)
```

---

## The 6 Modules

Each module is a plugin under `nanobot/ene/`. They register with `ModuleRegistry`, which aggregates their tools, context blocks, and lifecycle hooks. Modules can be added/removed/swapped without touching the core pipeline.

| # | Module | What It Does | Tools | Deep Dive |
|---|--------|-------------|-------|-----------|
| 1 | **Memory** | 5-section core memory (always in prompt, token-budgeted) + ChromaDB vector store for long-term recall + sleep agent that extracts facts during idle time | `save_memory`, `edit_memory`, `delete_memory`, `search_memory` | [MEMORY.md](MEMORY.md) |
| 2 | **Social** | Tracks people across platforms. Bayesian trust scoring with time gates. Trust tiers control what people can do (DM access needs `familiar`+ tier) | `update_person_note`, `view_person`, `list_people` | [SOCIAL.md](SOCIAL.md) |
| 3 | **Observatory** | Watches everything: LLM calls, costs, latency, errors. Live dashboard at `:18791`. Per-module event logging. Health alerts | `view_metrics`, `view_module` | [OBSERVABILITY.md](OBSERVABILITY.md) |
| 4 | **Watchdog** | Response quality auditing. **Currently disabled** to save costs | none | - |
| 5 | **Conversation Tracker** | Multi-thread awareness for group chats. Tracks which threads Ene is involved in, prevents re-replaying old messages, builds thread-aware context | none (internal) | - |
| 6 | **Daemon** | Free LLM pre-classifier. Runs on every message before the main LLM. Outputs RESPOND/CONTEXT/DROP + security flags. 5s timeout with math fallback | none (internal) | - |

### Module Lifecycle

```
Boot:   register() -> initialize(context) for each module
Per-message: set_sender() -> get_context_blocks() -> get_tools()
After response: notify_message(msg, responded=True)
Idle: notify_idle(seconds)     -- triggers memory sleep agent at 5 min
Daily: notify_daily()          -- triggers deep memory path at 4 AM
Shutdown: shutdown()
```

---

## Security

Everything here is enforced in Python, not by the LLM. Prompt injection can't bypass these.

| Layer | What | Details |
|-------|------|---------|
| **Trust root** | `DAD_IDS` in security.py | Hardcoded. Never from config or env. Dad = inner_circle (1.0) always |
| **Tool restriction** | `RESTRICTED_TOOLS` | filesystem, shell, spawn, cron, view_metrics, view_module — Dad only. Non-Dad callers never see these tools in the schema |
| **Rate limiting** | 10 msgs/30s | Per-sender, Dad exempt. Excess silently dropped before any processing |
| **Auto-mute** | Security flags | Daemon flags suspicious behavior -> 30 min auto-mute. Manual mute via tool (1-30 min) |
| **DM gate** | Trust >= 0.35, 14+ days | Only `familiar` tier and above can DM. Below that = friendly rejection, $0 cost |
| **Output sanitization** | `clean_response()` | ALL output goes through this. Strips reflection, paths, IDs, tool XML. Enforces length limits |
| **Anti-drift** | Re-anchoring | Identity reminder injected every 6 assistant turns. Fights persona drift in long sessions |
| **Guild whitelist** | `ALLOWED_GUILD_IDS` | Only Dad's Discord server. Other servers silently ignored |

---

## What's Configurable vs Hardcoded

### Configurable (config.json)

- `agents.defaults.model` — main LLM model (currently DeepSeek v3.2)
- `agents.defaults.consolidation_model` — separate model for diary/summaries
- `memory.core_token_budget` — core memory size (default: 4000 tokens)
- `memory.embedding_model` — for vector store embeddings
- `observatory.dashboard_port` — dashboard port (default: 18791)
- `channels.discord.token` — Discord bot token
- `channels.discord.allowFrom` — empty = allow everyone, or list of user IDs

### Identity Files (loaded fresh every message)

- `SOUL.md` — who Ene is (personality architecture)
- `AGENTS.md` — response rules (must stay in sync with loop.py behavior)
- `USER.md` — Dad's profile
- Changes take effect immediately, no restart needed

### Hardcoded (never change via config)

- `DAD_IDS` — trust root, in security.py
- `RESTRICTED_TOOLS` — Dad-only tools, in security.py
- `ALLOWED_GUILD_IDS` — guild whitelist, in discord.py
- Trust formula coefficients — in social/trust.py
- Debounce timing, rate limits — in loop.py

---

## How Do I...?

| I want to... | Go here |
|-------------|---------|
| Change Ene's personality | Edit `~/.nanobot/workspace/SOUL.md` (live reload) |
| Change the LLM model | `~/.nanobot/config.json` -> `agents.defaults.model` |
| See why she did/didn't respond | Dashboard at `localhost:18791/live`, or use `view_module signals` |
| See what she remembers | `~/.nanobot/workspace/memory/core.json`, or use `view_module memory` |
| Check API costs | Dashboard, or query `observatory.db` |
| Debug a specific message batch | Dashboard -> find the trace_id -> use `view_module` with `trace_id=` |
| Add a new module | Read [DEVELOPMENT.md](DEVELOPMENT.md) "Adding a New Ene Module" |
| Add a new channel (Reddit, etc.) | Read [DEVELOPMENT.md](DEVELOPMENT.md) "Adding a New Channel Adapter" |
| Understand trust math | Read [SOCIAL.md](SOCIAL.md) |
| Understand memory system | Read [MEMORY.md](MEMORY.md) |
| Run all tests | `python -m pytest tests/ -x -q` |
| Run just memory tests | `python -m pytest tests/ene/memory/ -q` |
| Run just social tests | `python -m pytest tests/ene/social/ -q` |
| Test behavioral changes safely | Use the lab: `nanobot lab run script.jsonl --snapshot my_snap` |
| Make a code change | Read [DEVELOPMENT.md](DEVELOPMENT.md) workflow |
| Check architectural constraints | Read [WHITELIST.md](WHITELIST.md) before touching anything |
| Understand the full long-term vision | Read [ENE_COMPLETE_REFERENCE.md](ENE_COMPLETE_REFERENCE.md) |

---

## What Happens When...?

### Someone @mentions Ene in Discord

1. Discord gateway receives MESSAGE_CREATE via WebSocket
2. `discord.py` filters (bot? wrong guild?) -> passes -> publishes to message bus
3. Rate limit check (non-Dad: 10/30s) -> passes
4. Added to debounce buffer for that channel. 3.5s of quiet -> buffer flushes
5. Daemon (free LLM) classifies the message. @mention = hard override to RESPOND
6. Conversation tracker ingests it, assigns to thread, builds context
7. Main LLM called with: system prompt + module contexts + conversation history + thread context
8. LLM may use tools (memory, search, etc.) in a loop (up to 20 iterations)
9. LLM outputs text -> `clean_response()` sanitizes it
10. Reply sent to Discord (threaded). Session updated. All modules notified

### A stranger tries to DM Ene

1. DM arrives via gateway -> no guild_id -> recognized as DM
2. Social module looks up sender by platform ID -> creates profile if new
3. Trust tier = `stranger` (score 0.0, needs >= 0.35 and 14+ days for `familiar`)
4. DM gate rejects: friendly message sent, no LLM called, $0 cost
5. The stranger needs to interact in the public server first, build trust over weeks

### Ene is idle for 5 minutes

1. Sleep agent's quick path triggers (idle threshold: 300 seconds)
2. Extracts facts and entities from recent conversation
3. Indexes into ChromaDB vector store (long-term memory)
4. Writes diary entry for today
5. Checks core memory budget utilization
6. At 4 AM: deep path runs -> reflections, contradiction detection, pruning

### Session gets too long

1. Session token estimate hits 50% of 60K budget -> consolidation triggers
2. LLM summarizes older messages into a running summary
3. At 80% budget -> auto-rotation: session cleared, summary injected as seed
4. Hybrid history: summary at top (older stuff) + recent 12 messages verbatim at bottom
5. This follows "Lost in the Middle" research: LLMs attend best to start and end of context

---

## The Doc Map

| Document | What it is | Read when... |
|----------|-----------|-------------|
| **SYSTEM_GUIDE.md** (this) | The map. Overview of everything | You're lost or onboarding |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Detailed pipeline spec with code-level flow | Debugging message flow |
| [DEVELOPMENT.md](DEVELOPMENT.md) | How to make changes safely, testing requirements | Before writing any code |
| [TESTING.md](TESTING.md) | Test infrastructure, lab usage, test patterns | Before writing tests |
| [MEMORY.md](MEMORY.md) | Memory system deep dive: core, vector, sleep agent | Touching memory code |
| [SOCIAL.md](SOCIAL.md) | Trust system: formula, tiers, time gates, research basis | Touching social code |
| [OBSERVABILITY.md](OBSERVABILITY.md) | Metrics, dashboard, module events, trace IDs | Debugging or adding instrumentation |
| [CAPABILITIES.md](CAPABILITIES.md) | What Ene can do, tool access table, platform details | Feature overview |
| [WHITELIST.md](WHITELIST.md) | Architectural decisions (append-only record) | Before any refactor or new pattern |
| [CHANGELOG.md](CHANGELOG.md) | Every change since fork, chronological | Understanding history |
| [CODING_PATTERNS.md](CODING_PATTERNS.md) | Code templates: tools, modules, tests, error handling | Before writing code (adopted from upstream nanobot) |
| [ENE_COMPLETE_REFERENCE.md](ENE_COMPLETE_REFERENCE.md) | Full vision, philosophy, roadmap, endgame architecture | Big-picture direction |
| `CLAUDE.md` | Instructions for Claude Code AI sessions | You don't read this — it's for the AI |

---

## Quick Reference

### Commands

```bash
python -m nanobot                         # Start Ene
python -m pytest tests/ -x -q            # Run all tests
python -m pytest tests/ene/memory/ -q    # Memory tests only
python -m pytest tests/ene/social/ -q    # Social tests only
python -m pytest tests/ene/conversation/ -q  # Conversation tests
nanobot lab snapshot create my_snap --from live   # Snapshot live state
nanobot lab run script.jsonl --snapshot my_snap   # Run lab test
```

### Key Numbers

| What | Value |
|------|-------|
| Debounce window | 3.5 seconds |
| Batch size limit | 15 messages (40 hard cap) |
| LLM timeout | 45 seconds |
| Rate limit (non-Dad) | 10 msgs / 30 seconds |
| Core memory budget | 4,000 tokens |
| Session token budget | 60,000 tokens |
| Consolidation trigger | 50% of budget |
| Auto-rotation trigger | 80% of budget |
| Re-anchoring interval | Every 6 assistant turns |
| DM access threshold | Trust >= 0.35, 14+ days |
| Daemon timeout | 5 seconds |
| Auto-mute (security) | 30 minutes |
| Dashboard port | 18791 |

### Trust Tiers

```
stranger     (0.00 - 0.14)  Polite, guarded. No DMs.
acquaintance (0.15 - 0.34)  Friendly, cautious. No DMs.
familiar     (0.35 - 0.59)  Warm, shares opinions. CAN DM. (min 14 days)
trusted      (0.60 - 0.79)  Open, shares freely.
inner_circle (0.80 - 1.00)  Full trust. Dad is always here.
```
