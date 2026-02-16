# Ene Growth Changelog

All notable changes to Ene's systems, behavior, and capabilities.

---

## [2026-02-16] — Context Window & Pre-Launch Hardening

**Context:** Final fixes before Ene goes live on Discord. Research-backed improvements to context management, persona drift prevention, and response cleaning. Based on comprehensive study of industry systems (Character.AI, Kindroid, ChatGPT, JanitorAI), academic papers (MemGPT, "Lost in the Middle", Recursive Summarization, StreamingLLM), and DeepSeek v3.2 specific behavior.

### Fixed — Reflection Stripping
- **Comprehensive regex** — Previous regex only caught `## Reflection` exactly. Now catches:
  - All heading levels (`##`, `###`, `####`)
  - Words around keywords (`## My Reflection`, `## Internal Thoughts`)
  - Case variations (`REFLECTION`, `reflection`, `Reflection`)
  - Bold-only headers (`**Reflection**`, `**Internal Thoughts**`)
  - Inline reflection paragraphs (`Let me reflect...`, `Upon reflection...`, `Note to self:`)
  - Keywords: Reflection, Internal, Thinking, Analysis, Self-Assessment, Observations, Note to Self
- **43 new tests** in `tests/test_context_window.py`

### Fixed — Consolidation Trigger
- **Count responded exchanges, not lurked messages** — Previous trigger used `len(session.messages) > memory_window` which counted ALL messages including lurked. In a busy Discord server, 50 lurked messages = minutes of banter, triggering consolidation constantly.
- **Dual trigger**: Now fires on EITHER responded count > memory_window OR estimated tokens > 50% of budget (30K tokens)
- **Token warning** at 80% budget utilization

### Added — Hybrid Context Window
- **Running summaries** of older conversation (recursive summarization pattern from MemGPT/Wang et al. 2023)
- **Recent verbatim window** — Last 20 messages kept word-for-word
- **"Lost in the Middle" layout** (Liu et al. 2024) — Summary placed FIRST in history (top of middle zone, moderate attention), recent messages placed LAST (near current message, high attention zone)
- Summary auto-generated and cached per session key, cleared on `/new`

### Added — Identity Re-Anchoring
- **Periodic personality injection** every 10 assistant responses to fight persona drift
- Research: DeepSeek v3.2 documented to drift 30%+ after 8-12 turns
- Brief reminder injected as system message in the high-attention zone (between history and current message)
- Doesn't bloat context (single short sentence)

### Added — Auto-Session Management
- **Token-based compaction** — Sessions now estimated via chars/4 heuristic
- **50% budget** = begin summarization, **80% budget** = warning log
- `Session.estimate_tokens()` and `Session.get_responded_count()` helper methods

### Modified
- **`nanobot/agent/loop.py`** — Reflection regex rewrite, smart consolidation trigger, hybrid history assembly, running summary generation, re-anchoring check, token-based compaction
- **`nanobot/agent/context.py`** — `build_messages()` now accepts `reanchor` parameter, injects system message near end of history
- **`nanobot/session/manager.py`** — `get_hybrid_history()`, `estimate_tokens()`, `get_responded_count()` on Session

### Test Results
- 389 tests passing (43 new context window + 346 existing)
- Zero regressions

---

## [2026-02-16] — Tool Hiding & Identity Split

**Context:** Pre-launch hardening. Non-Dad users were seeing all tools (wasting tokens, causing "Access denied" weirdness) and Ene was leaking technical details (file names, workspace paths) when asked about herself.

### Added — Caller-Aware Tool Filtering
- **`get_definitions_for_caller()`** on ToolRegistry — Non-Dad callers get filtered tool list excluding RESTRICTED_TOOLS. LLM never sees they exist, saving tokens and preventing awkward exchanges.

### Added — Split Identity Blocks
- **`_get_identity_full()`** — Full technical identity for Dad (workspace paths, file locations, all tools, architecture details)
- **`_get_identity_public()`** — Stripped version for everyone else:
  - No file paths, framework names, or architecture details
  - Explicit instructions on how to talk about herself naturally
  - "I have my own personality" not "I have a SOUL.md file"
  - "I remember things" not "I store memories in ChromaDB"

### Modified
- **`nanobot/agent/tools/registry.py`** — `get_definitions_for_caller()`
- **`nanobot/agent/context.py`** — `_is_dad_caller()`, `_get_identity_full()`, `_get_identity_public()`
- **`nanobot/agent/loop.py`** — Uses filtered tool definitions in `_run_agent_loop()`

---

## [2026-02-16] — Social Module (People + Trust)

**Context:** Ene needs to know WHO she's talking to before going live. Built a research-backed trust scoring system with people profiles, Bayesian reputation, and a social graph. Designed from psychology/sociology research (Josang 2002, Slovic 1993, Eagle 2009, Hall 2019, Dunbar 1992, Lewicki & Bunker 1995) and industry systems (eBay, Uber, StackOverflow, MMO guilds).

### Added — Social Module (`nanobot/ene/social/`)
- **PersonProfile + PersonRegistry** (`person.py`) — File-per-person storage in `memory/social/people/`, O(1) platform ID lookup via `index.json`, auto-created Dad profile with max trust, CRUD with disk persistence, notes, aliases, connections
- **TrustCalculator** (`trust.py`) — Hybrid Bayesian + temporal modulator scoring:
  - Beta Reputation core: `(pos+1)/(pos+neg*3+2)` (starts uncertain at 0.5)
  - Geometric mean of 4 modulators: tenure, consistency, session depth, timing entropy
  - 3:1 asymmetric weighting for negative events (Slovic 1993)
  - Time gates: acquaintance=3d, familiar=14d, trusted=60d, inner_circle=180d (Hall 2019)
  - Exponential decay: 60-day half-life, floored at 50% of original score
  - Anti-gaming: geometric mean prevents one-dimensional signal inflation
  - Dad hardcoded at 1.0/inner_circle, immutable
- **SocialGraph** (`graph.py`) — Connection queries, mutual friends, BFS shortest path (max depth configurable), context rendering
- **3 social tools** (`tools.py`):
  - `update_person_note(person_name, note)` — Record things about people
  - `view_person(person_name)` — Full profile with trust, notes, connections
  - `list_people()` — All known people sorted by trust score
- **SocialModule** (`__init__.py`) — EneModule entry point with person card injection, interaction recording, daily maintenance (decay + history snapshots)
- **DM access gate** — Only `familiar` tier (score >= 0.35, 14+ days known) can DM Ene. Below that → system rejection, no LLM call, zero cost
- **143 unit tests** across 5 test files (`tests/ene/social/`)

### Modified
- **`nanobot/ene/__init__.py`** — Sender identity bridge: `set_current_sender()`, `get_current_platform_id()`, `get_current_metadata()`, `get_module()` on ModuleRegistry. `set_sender_context()` default on EneModule. Updated `get_all_dynamic_context()` to call sender context before collecting blocks.
- **`nanobot/agent/loop.py`** — SocialModule registration in `_register_ene_modules()`. Sender wiring (`set_current_sender()`) in `_process_message()`. DM access gate check before LLM call with `_is_dm()` and `_dm_access_allowed()` helpers.
- **`nanobot/config/schema.py`** — Added `SocialConfig` class (enabled, decay_inactive_days, decay_rate_per_day, sentiment_analysis). Added to `AgentDefaults`.
- **`nanobot/agent/context.py`** — Social tools documentation in identity block.

### Test Results
- 346 tests passing (143 social + 148 memory + 55 existing)
- Zero regressions

---

## [2026-02-16] — Memory System v2

**Context:** Complete redesign of Ene's memory system. Replaced append-only MEMORY.md with structured, editable core memory + ChromaDB vector store + sleep-time background processing. Built on a modular plugin architecture for future subsystem modules (personality, goals, timeline, etc.).

### Added — Module Architecture (`nanobot/ene/`)
- **EneModule base class** — Abstract interface for all Ene subsystems (tools, context, lifecycle hooks)
- **EneContext** — Shared context (workspace, provider, config, bus, sessions) passed to all modules
- **ModuleRegistry** — Aggregates tools, context blocks, and broadcasts lifecycle events (message, idle, daily) to all modules with error isolation
- **13 unit tests** for ModuleRegistry (`tests/ene/test_module_registry.py`)

### Added — Memory Module (`nanobot/ene/memory/`)
- **CoreMemory** (`core_memory.py`) — Structured JSON memory with 5 sections (identity, people, preferences, context, scratch), 4000 token budget via tiktoken, 6-char hex entry IDs for edit/delete
- **VectorMemory** (`vector_memory.py`) — ChromaDB with 3 collections (memories, entities, reflections), three-factor retrieval scoring (similarity 50% + recency 25% + importance 25%), Ebbinghaus-inspired memory decay
- **EneEmbeddings** (`embeddings.py`) — litellm.embedding() wrapper with automatic fallback to ChromaDB default embeddings
- **SleepTimeAgent** (`sleep_agent.py`) — Background processor with dual triggers:
  - Quick path (5 min idle): fact extraction, entity tracking, diary writing
  - Deep path (daily 4 AM): reflection generation, contradiction detection, weak memory pruning, core budget review
- **MemorySystem facade** (`system.py`) — Coordinates core memory, vector memory, diary, entity cache, and automatic migration from legacy MEMORY.md/CORE.md
- **4 memory tools** (`tools.py`):
  - `save_memory(memory, section, importance)` — Add to core memory
  - `edit_memory(entry_id, new_content, new_section, importance)` — Edit by ID
  - `delete_memory(entry_id, archive=True)` — Remove from core, optionally archive to vector
  - `search_memory(query, memory_type, limit)` — Search long-term memory
- **MemoryModule** (`__init__.py`) — Module entry point implementing EneModule interface
- **135 unit tests** across 7 test files (`tests/ene/memory/`)

### Modified
- **`nanobot/agent/loop.py`** — Added ModuleRegistry integration: `_register_ene_modules()`, `_initialize_ene_modules()`, idle watcher background task, daily trigger background task, module lifecycle notifications on message/idle/daily/shutdown
- **`nanobot/agent/context.py`** — Accepts ModuleRegistry for context injection. System prompt now includes structured core memory (with IDs and budget) + dynamic per-message retrieval (vector search + entity context). Updated memory instructions for new tools.
- **`nanobot/config/schema.py`** — Added `MemoryConfig` class with `core_token_budget`, `embedding_model`, `chroma_path`, `idle_trigger_seconds`, `daily_trigger_hour`, `diary_context_days`. Added to `AgentDefaults`.
- **`nanobot/cli/commands.py`** — Both `gateway` and `agent` commands now pass `config=config` to AgentLoop for module initialization.

### Test Results
- 203 tests passing (148 Ene module tests + 55 existing tests)
- Zero regressions

---

## [2026-02-16] — Fork & Foundation

**Context:** Forked nanobot (HKUDS/nanobot v0.1.3.post7) to IriaiSan/Ene. Replaced pip install with editable local clone at `C:\Users\Ene\Ene\`. Set up upstream tracking for selective updates.

### Added
- **DAD_IDS + RESTRICTED_TOOLS** (`loop.py`): Hardcoded Dad's Discord (`1175414972482846813`) and Telegram (`8559611823`) IDs. Restricted tools (`exec`, `write_file`, `edit_file`, `read_file`, `list_dir`, `spawn`, `cron`) are blocked for all non-Dad users with "Access denied."
- **_should_respond()** (`loop.py`): Lurk/respond filtering. Ene responds to: Dad (always), DMs, messages containing "ene". All other public messages are stored in session history silently for context.
- **_ene_clean_response()** (`loop.py`): Outbound response sanitizer that strips:
  - `## Reflection` and similar internal monologue blocks
  - Leaked file paths (`C:\Users\...`, `/home/...`)
  - Leaked platform IDs (`discord:123...`, `telegram:456...`)
  - Stack traces and LLM error strings
  - Markdown bold (`**text**`) in public channels
- **Response length enforcement** (`loop.py`): Public channels capped at 500 chars (sentence-boundary truncation). Hard Discord limit at 1900 chars.
- **Error suppression** (`loop.py`): Exceptions during message processing are logged but never sent to public chat. Dad gets a short error summary in DMs only.
- **Image handling** (`discord.py`): Image attachments are not downloaded (DeepSeek v3 has no vision). Replaced with text: `[username sent an image: filename.png]`.
- **Reply threading** (`loop.py`): Responses are threaded as replies to the original message via Discord's `message_reference`.
- **Display name capture** (`discord.py`): Discord nickname/username extracted and passed in metadata for context-aware lurking and responses.
- **Consolidation hardening** (`loop.py`): Memory consolidation retries up to 2 times on JSON parse failure, dropping 10 oldest messages from buffer each attempt. Force-advances consolidation pointer on final failure to prevent infinite retry loops.

### Infrastructure
- Forked `HKUDS/nanobot` to `IriaiSan/Ene`
- Cloned to `C:\Users\Ene\Ene\`
- Installed as editable (`pip install -e .`)
- Added `upstream` remote for tracking original repo updates
- Git identity: Iitai / Litai-w-@hotmail.com
