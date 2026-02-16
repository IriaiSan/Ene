# Ene — Architecture Reference

## Overview

Ene is an AI companion built on top of [nanobot](https://github.com/HKUDS/nanobot), a Python framework for LLM agents with multi-platform chat integration. The codebase is a fork with Ene-specific modifications applied directly to the nanobot source.

- **Runtime:** Python 3.11 on Windows 11 (ThinkCentre M710Q)
- **LLM:** DeepSeek v3.2 via OpenRouter
- **Platforms:** Discord (public server + DMs), Telegram (Dad only)

## Directory Layout

```
C:\Users\Ene\
├── Ene\                          # Git repo (fork of HKUDS/nanobot)
│   ├── nanobot\                  # Source code (editable install)
│   │   ├── agent\
│   │   │   ├── loop.py           # Core message processing (Ene mods here)
│   │   │   ├── context.py        # System prompt builder
│   │   │   ├── memory.py         # Legacy MEMORY.md / HISTORY.md persistence
│   │   │   ├── skills.py         # Skill loading system
│   │   │   ├── subagent.py       # Background task agents
│   │   │   └── tools\            # Tool implementations
│   │   ├── ene\                  # Ene subsystem modules
│   │   │   ├── __init__.py       # EneModule base, EneContext, ModuleRegistry
│   │   │   ├── memory\           # Module 1: Memory system
│   │   │   └── social\           # Module 2: People + Trust
│   │   │       ├── __init__.py   # MemoryModule entry point
│   │   │       ├── core_memory.py    # Editable core memory (JSON, token-budgeted)
│   │   │       ├── vector_memory.py  # ChromaDB vector store (3 collections)
│   │   │       ├── embeddings.py     # Embedding provider (litellm + fallback)
│   │   │       ├── sleep_agent.py    # Background processor (idle + daily)
│   │   │       ├── system.py         # MemorySystem facade
│   │   │       └── tools.py          # 4 memory tools
│   │   ├── channels\
│   │   │   ├── base.py           # Base channel interface
│   │   │   └── discord.py        # Discord gateway + REST (Ene mods here)
│   │   ├── bus\                  # Async message queue
│   │   ├── session\              # Conversation history (JSONL files)
│   │   ├── providers\            # LLM provider abstraction (LiteLLM)
│   │   ├── config\               # Configuration schema
│   │   └── cron\                 # Scheduled tasks
│   ├── tests\
│   │   └── ene\                  # Ene module tests
│   │       ├── test_module_registry.py
│   │       ├── memory\           # Memory module tests (148 tests)
│   │       └── social\           # Social module tests (143 tests)
│   └── docs\                     # Ene documentation (you are here)
│
└── .nanobot\                     # Runtime data (not in git)
    ├── config.json               # API keys, channel config, model settings
    ├── workspace\
    │   ├── SOUL.md               # Personality definition
    │   ├── AGENTS.md             # Agent instructions
    │   ├── memory\
    │   │   ├── core.json         # Core memory (structured, token-budgeted)
    │   │   ├── diary\            # Daily diary entries (YYYY-MM-DD.md)
    │   │   └── logs\             # Interaction logs per channel
    │   └── chroma_db\            # ChromaDB vector store (long-term memory)
    └── sessions\                 # Conversation JSONL files per channel
```

## Message Pipeline

```
Discord User sends message
        │
        ▼
[Discord Gateway WebSocket]        channels/discord.py
  _handle_message_create()
  ├── Filter bots
  ├── Check allowFrom
  ├── Extract display name
  ├── Handle attachments (images → text description)
  ├── Start typing indicator
  └── Publish InboundMessage to bus
        │
        ▼
[Message Bus]                      bus/queue.py
  Async queue connecting channels to agent
        │
        ▼
[Agent Loop]                       agent/loop.py
  _process_message()
  ├── Set _current_caller_id (for tool permissions)
  ├── Set current sender on ModuleRegistry (for social context)
  ├── _should_respond() — lurk or respond?
  │   ├── Dad → always respond
  │   ├── DM → always respond
  │   ├── Contains "ene" → respond
  │   └── Otherwise → store in session, return None
  ├── DM access gate — block untrusted DMs (zero LLM cost)
  │   ├── Is DM? (Discord: no guild_id, Telegram: not group)
  │   ├── Trust tier < familiar? → friendly rejection, return
  │   └── Dad always passes
  ├── Check slash commands (/new, /help)
  ├── Trigger consolidation if buffer > memory_window
  ├── Build context (system prompt + history + current message)
  │   └── Person card injected via social module (name, tier, stats)
  ├── Call LLM via provider
  ├── Tool execution loop (with RESTRICTED_TOOLS check)
  ├── Store raw response in session
  ├── _ene_clean_response() — sanitize output
  └── Notify modules (social records interaction, updates trust)
        │
        ▼
[Outbound Message Bus]
        │
        ▼
[Discord REST API]                 channels/discord.py
  send() — POST to Discord with reply threading
```

## Key Security Mechanisms

### Tool Restrictions (Code-Level)
```python
DAD_IDS = {"telegram:8559611823", "discord:1175414972482846813"}
RESTRICTED_TOOLS = {"exec", "write_file", "edit_file", "read_file", "list_dir", "spawn", "cron"}
```
Non-Dad callers get "Access denied." for any restricted tool. This is enforced in Python, not by the LLM — it cannot be bypassed by prompt injection.

### Response Sanitization
`_ene_clean_response()` runs on every outbound message:
- Strips reflection blocks (comprehensive regex: all heading levels, bold, inline, case-insensitive — catches `## Reflection`, `### Internal Thoughts`, `**Analysis**`, `Let me reflect...`, `Note to self:`, etc.)
- Strips file paths, platform IDs, stack traces
- Removes markdown bold in public channels
- Enforces 500 char limit (public) / 1900 char limit (Discord hard cap)

### Error Suppression
Exceptions during message processing are caught in `run()`. Public chat sees nothing. Dad gets a short error summary.

## Configuration

Main config: `C:\Users\Ene\.nanobot\config.json`

Key settings:
- `agents.defaults.model`: `deepseek/deepseek-v3.2`
- `agents.defaults.memoryWindow`: `50` (messages before consolidation)
- `agents.defaults.maxTokens`: `8192`
- `channels.discord.allowFrom`: `[]` (empty = allow everyone)
- `channels.telegram.allowFrom`: `["8559611823"]` (Dad only)
- `providers.openrouter.apiKey`: OpenRouter API key

## Module Architecture

Ene subsystems (memory, personality, goals, etc.) are modular plugins under `nanobot/ene/`. Each implements `EneModule` and registers with `ModuleRegistry`. The registry auto-aggregates tools, context blocks, and lifecycle hooks.

Adding a new module:
1. Create folder in `nanobot/ene/`
2. Implement `EneModule` interface
3. Register in `AgentLoop._register_ene_modules()`

## Memory System (v2)

See `docs/MEMORY.md` for full reference.

### Core Memory (`core.json`)
- Structured JSON with 5 sections (identity, people, preferences, context, scratch)
- 4000 token budget enforced by tiktoken
- Each entry has a 6-char hex ID for edit/delete
- Always in system prompt — Ene curates what stays

### Long-term Memory (ChromaDB)
- Three collections: memories, entities, reflections
- Three-factor retrieval scoring: similarity (50%) + recency (25%) + importance (25%)
- Ebbinghaus-inspired decay for memory strength
- Entity name cache for automatic context injection

### Sleep Agent (Background)
- Quick path (5 min idle): fact extraction, entity tracking, diary writing
- Deep path (daily 4 AM): reflections, contradiction detection, pruning, core budget review

### Memory Tools
- `save_memory` — Add to core memory (section + importance)
- `edit_memory` — Edit core entry by ID
- `delete_memory` — Remove from core (optional archive to vector store)
- `search_memory` — Search long-term vector memory

## Social System (People + Trust)

See `docs/SOCIAL.md` for full reference.

### People Profiles
- One JSON file per person in `memory/social/people/`
- Platform ID → Person ID index for O(1) lookup
- Auto-created on first interaction, Dad pre-seeded on init
- Notes, aliases, connections tracked per person

### Trust Scoring
- Bayesian core (Beta Reputation System) with temporal modulators
- 5 tiers: stranger → acquaintance → familiar → trusted → inner_circle
- Time gates prevent speed-running trust (minimum days per tier)
- Geometric mean of 4 signals prevents one-dimensional gaming
- 3:1 asymmetric negative weighting — trust breaks faster than it builds
- Exponential decay for inactive users (60-day half-life, 50% floor)
- Dad hardcoded at 1.0/inner_circle, immutable

### DM Access Gate
- Only `familiar` tier (14+ days, score >= 0.35) can DM Ene
- Below that → system rejection message, no LLM call, zero cost
- Enforced at pipeline level before any LLM processing

### Social Tools
- `update_person_note` — Record things about people
- `view_person` — View full profile
- `list_people` — List everyone with trust tiers

### Context Window Management
- **Hybrid history**: Recent 20 messages verbatim + running summary of older conversation
- **"Lost in the Middle" layout**: Summary at top of history (moderate attention), recent at bottom (high attention)
- **Running summaries**: Recursive summarization of older messages, cached per session key
- **Identity re-anchoring**: Brief personality reminder injected every 10 assistant responses (high-attention zone between history and current message)
- **Token estimation**: `Session.estimate_tokens()` using chars/4 heuristic

### Smart Consolidation
- **Dual trigger**: Fires when EITHER responded count > memory_window OR token estimate > 50% of budget (30K tokens)
- **Responded count**: Only counts Ene's actual responses, not lurked messages (fixes busy server false triggers)
- **Token warning**: Logs warning at 80% budget utilization, suggests `/new`
- LLM summarizes old messages into diary entry
- On failure: retries twice, dropping 10 oldest messages each time

## Session Management

- Session key format: `{channel}:{chat_id}` (e.g., `discord:1306235136400035916`)
- Stored as JSONL files in `~/.nanobot/sessions/`
- Each message: `{"role": "user"|"assistant", "content": "...", "timestamp": "...", "tools_used": [...]}`
- `/new` command: archives all messages via consolidation, clears running summary, starts fresh session
- **Running summaries**: Cached in memory per session key. Generated via recursive summarization when session grows large enough. Cleared on `/new`.
- **Token budget**: 60K tokens allocated for history. Compaction starts at 50%, warning at 80%.

## Git Workflow

- **origin**: `github.com/IriaiSan/Ene` (our fork)
- **upstream**: `github.com/HKUDS/nanobot` (original repo)
- Editable install: changes to `C:\Users\Ene\Ene\nanobot\` take effect immediately
- To check for upstream updates: `git fetch upstream`
- To selectively merge: `git cherry-pick <commit>` or `git merge upstream/main`
