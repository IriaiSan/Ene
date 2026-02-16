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
│   │   │   ├── memory.py         # MEMORY.md / HISTORY.md persistence
│   │   │   ├── skills.py         # Skill loading system
│   │   │   ├── subagent.py       # Background task agents
│   │   │   └── tools\            # Tool implementations
│   │   ├── channels\
│   │   │   ├── base.py           # Base channel interface
│   │   │   └── discord.py        # Discord gateway + REST (Ene mods here)
│   │   ├── bus\                  # Async message queue
│   │   ├── session\              # Conversation history (JSONL files)
│   │   ├── providers\            # LLM provider abstraction (LiteLLM)
│   │   ├── config\               # Configuration schema
│   │   └── cron\                 # Scheduled tasks
│   └── docs\                     # Ene documentation (you are here)
│
└── .nanobot\                     # Runtime data (not in git)
    ├── config.json               # API keys, channel config, model settings
    ├── workspace\
    │   ├── SOUL.md               # Personality definition
    │   ├── AGENTS.md             # Agent instructions
    │   ├── memory\
    │   │   ├── MEMORY.md         # Long-term facts (loaded every prompt)
    │   │   └── HISTORY.md        # Event log (grep-searchable)
    │   └── people\               # Per-user profile files (planned)
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
  ├── _should_respond() — lurk or respond?
  │   ├── Dad → always respond
  │   ├── DM → always respond
  │   ├── Contains "ene" → respond
  │   └── Otherwise → store in session, return None
  ├── Check slash commands (/new, /help)
  ├── Trigger consolidation if buffer > memory_window
  ├── Build context (system prompt + history + current message)
  ├── Call LLM via provider
  ├── Tool execution loop (with RESTRICTED_TOOLS check)
  ├── Store raw response in session
  └── _ene_clean_response() — sanitize output
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
- Strips `## Reflection` blocks
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

## Memory System

### MEMORY.md (Long-term)
- Loaded into every system prompt
- Contains: identity, people notes, rules, known limitations
- Updated during consolidation

### HISTORY.md (Event Log)
- Append-only, grep-searchable
- Each entry: `[YYYY-MM-DD HH:MM] Summary paragraph`
- Used for recalling past events

### Consolidation
- Triggers when session messages exceed `memory_window` (50)
- LLM summarizes old messages into history entry + memory update
- On JSON parse failure: retries twice, dropping 10 oldest messages each time
- On total failure: force-advances pointer to prevent infinite retries

## Session Management

- Session key format: `{channel}:{chat_id}` (e.g., `discord:1306235136400035916`)
- Stored as JSONL files in `~/.nanobot/sessions/`
- Each message: `{"role": "user"|"assistant", "content": "...", "timestamp": "...", "tools_used": [...]}`
- `/new` command: archives all messages via consolidation, starts fresh session

## Git Workflow

- **origin**: `github.com/IriaiSan/Ene` (our fork)
- **upstream**: `github.com/HKUDS/nanobot` (original repo)
- Editable install: changes to `C:\Users\Ene\Ene\nanobot\` take effect immediately
- To check for upstream updates: `git fetch upstream`
- To selectively merge: `git cherry-pick <commit>` or `git merge upstream/main`
