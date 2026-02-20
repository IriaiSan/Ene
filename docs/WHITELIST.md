# Ene Technical Decision Whitelist

**This document is APPEND-ONLY.** New decisions are added at the bottom of the relevant section. Existing entries are NEVER modified or deleted. If a decision needs to be overridden, add an entry to the Exemptions section referencing the original decision number.

**Every new feature, refactor, or technical change MUST be checked against this whitelist before planning begins.** If something conflicts, ask the user to approve an exemption.

---

## Language & Runtime

| # | Decision | Reasoning | Date |
|---|----------|-----------|------|
| L1 | Python 3.11+ only | Match nanobot upstream; type hints, asyncio improvements | 2026-02-18 |
| L2 | No new runtime dependencies without approval | Keep install light; every pip package is attack surface | 2026-02-18 |
| L3 | Async-first (asyncio) for all I/O | Nanobot pattern; Discord/Telegram are async; blocking = dead bot | 2026-02-18 |
| L4 | English only in code, comments, docstrings | Consistency; Ene's English enforcement mirrors this | 2026-02-18 |

---

## Code Structure

| # | Decision | Reasoning | Date |
|---|----------|-----------|------|
| S1 | No file > 500 lines | loop.py at 2287 lines is unmaintainable; split into focused modules | 2026-02-18 |
| S2 | One class per file (exceptions: dataclasses, small helpers) | Easier to find, easier to test, easier for Ene to modify later | 2026-02-18 |
| S3 | Pure functions over methods when no instance state needed | Testable without mocking; extractable; Ene can reason about them | 2026-02-18 |
| S4 | Explicit imports, no wildcard (`from x import *`) | Debugging needs to know where things come from | 2026-02-18 |
| S5 | All Ene-specific code lives under `nanobot/ene/` or `nanobot/agent/` | Clear separation from upstream nanobot; easier merge conflicts | 2026-02-18 |
| S6 | Constants in dedicated module (`security.py` for security constants, etc.) | No magic numbers scattered across files | 2026-02-18 |
| S7 | Type hints on all function signatures | Self-documenting; catches bugs early; helps Claude understand code | 2026-02-18 |
| S8 | Import order: `__future__` → stdlib → typing/TYPE_CHECKING → loguru → project → `if TYPE_CHECKING:` block | Consistent across all files; prevents circular imports; one pattern to follow | 2026-02-19 |
| S9 | Tools return error strings from `execute()`, never raise exceptions | LLM reads return value — needs human-readable feedback, not stack traces | 2026-02-19 |
| S10 | Module heavy imports inside `initialize()`, not top-level | Prevents circular imports at startup; heavy deps loaded only when needed | 2026-02-19 |
| S11 | File encoding: always `encoding="utf-8"` on `open()` calls | Windows crashes on non-ASCII without explicit encoding | 2026-02-19 |
| S12 | No silent exception swallowing — minimum: `except Exception: logger.warning(...)` | Silent failures are invisible bugs; always log something | 2026-02-19 |

---

## Architecture & Modularity

| # | Decision | Reasoning | Date |
|---|----------|-----------|------|
| A1 | Module registry is the only way to add capabilities to Ene | Enforces boundaries; prevents spaghetti imports | 2026-02-18 |
| A2 | Modules declare permissions: `can_modify_self`, `can_connect_channels`, `can_access_tools` | Ene can autonomously add a Reddit module but CAN'T modify intake stream | 2026-02-18 |
| A3 | Core systems (intake, agent loop, security, session) are LOCKED — Ene cannot modify them | Prevents self-sabotage; only Dad + Claude Code can change core | 2026-02-18 |
| A4 | New services (Reddit, Twitter, etc.) connect via standard channel interface (`BaseChannel`) | Plug-and-play; Ene figures out the API, writes the adapter | 2026-02-18 |
| A5 | Conversation tracker owns message content; session stores lightweight markers only | Prevents dual-source duplication (the bug we just fixed) | 2026-02-18 |
| A6 | All module state persists to disk (JSON/SQLite) — no in-memory-only state | Bot restarts shouldn't lose data; debuggable via file inspection | 2026-02-18 |
| A7 | Module hot-swap: non-core modules can be enabled/disabled at runtime via config | Dad can flip switches without restarting; Ene can disable her own non-core modules | 2026-02-18 |
| A8 | Lab isolation via path overrides, not code forks — single codebase, zero drift | One codebase to maintain; lab and live always in sync | 2026-02-19 |
| A9 | MockChannel in `nanobot/channels/` follows BaseChannel pattern | Same `_handle_message()` path as real channels | 2026-02-19 |
| A10 | RecordReplayProvider in `nanobot/providers/` follows LLMProvider ABC | Wraps real provider; transparent to agent loop | 2026-02-19 |
| A11 | Lab infrastructure in `nanobot/lab/` — separate from core, does not modify locked code | Lab can evolve independently without touching production paths | 2026-02-19 |
| A12 | Module-level observability via `module_events` SQLite table + `ModuleMetrics` class | Per-module structured events enable debugging WHY decisions were made (vs operational metrics that only show WHAT happened) | 2026-02-19 |
| A13 | Trace ID generated per batch in `_process_batch()`, propagated to all `ModuleMetrics` instances | Links all module events across the full pipeline for one message batch — enables cross-module tracing | 2026-02-19 |
| A14 | Prompts extracted from source to `.txt` files in `nanobot/agent/prompts/` with `PromptLoader` | Version-tracked, file-based prompts enable A/B testing and behavioral correlation via manifest.json version tag | 2026-02-19 |
| A15 | Module instrumentation uses module-level `_metrics` + `set_metrics()` pattern (not instance attributes) | Avoids modifying class __init__ signatures; metrics wiring happens in loop.py during module initialization | 2026-02-19 |

---

## Security

| # | Decision | Reasoning | Date |
|---|----------|-----------|------|
| X1 | DAD_IDS is the only identity authority — hardcoded, never from config/env | Config can be tampered; hardcoded IDs are the trust root | 2026-02-18 |
| X2 | All user-facing text goes through `clean_response()` before sending | One chokepoint for sanitization; no bypass paths | 2026-02-18 |
| X3 | Tool XML (`<function_calls>`, `<invoke>`, `<parameter>`) MUST be stripped from output | LLM leaks internal markup; users should never see it | 2026-02-18 |
| X4 | Non-Dad users cannot use system tools (exec, file ops, spawn, cron, metrics) | Prevents strangers from running commands on the machine | 2026-02-18 |
| X5 | Mute system caps at 30 minutes — no permanent bans via tool | Prevents Ene from permanently silencing someone in a bad mood | 2026-02-18 |
| X6 | Rate limiting is per-user, not per-channel | Prevents one spammer from burning tokens for everyone | 2026-02-18 |
| X7 | Dad is exempt from rate limiting and muting | Owner always has access | 2026-02-18 |

---

## LLM & Cost

| # | Decision | Reasoning | Date |
|---|----------|-----------|------|
| C1 | DeepSeek v3.2 via OpenRouter as primary model | Best quality/cost ratio; Dad chose it | 2026-02-18 |
| C2 | Daemon uses free model rotation (Trinity, GLM 4.5, DeepSeek R1) | Pre-classification should cost $0 | 2026-02-18 |
| C3 | Math classifier (`signals.py`) preferred over LLM for classification when possible | <1ms, zero tokens, deterministic | 2026-02-18 |
| C4 | Observatory tracks every LLM call — model, tokens, cost, latency | Can't optimize what you can't measure | 2026-02-18 |
| C5 | Message tool stops the agent loop — max 1 response per batch | Prevents token waste from multi-message loops | 2026-02-18 |
| C6 | Public channel responses capped at 500 chars | Ene shouldn't write essays in group chat | 2026-02-18 |
| C7 | Main LLM has fallback model rotation (DeepSeek → Qwen 3 → Gemini Flash) | Prevents indefinite blocking when primary model is down | 2026-02-19 |
| C8 | 45s timeout per LLM call, retry once with next model | Balance between patience and not hanging forever | 2026-02-19 |
| C9 | Queue merge: backlogged batches collapse into one mega-batch | Ene catches up by reading everything at once, not replying to stale batches one by one | 2026-02-19 |
| C10 | Debounce window 3.5s, batch limit 15, buffer cap 40 | Longer window = fewer small batches; queue merge handles overflow | 2026-02-19 |
| C11 | Model recovery: after 5 min cooldown, probe primary on next chat(); snap back on success | Prevents permanent degradation to fallback after transient outages | 2026-02-19 |
| C12 | Diary consolidation uses Gemini 3 Flash (separate from main model) | Different model breaks pattern-lock; terse factual prompt prevents flowery prose | 2026-02-19 |
| C13 | DEFAULT_FALLBACK_MODELS[0] must match config.json model | Prevents silent model switch on deployment; config is source of truth for primary model | 2026-02-19 |

---

## Testing

| # | Decision | Reasoning | Date |
|---|----------|-----------|------|
| T1 | All new code must have tests | No untested code ships | 2026-02-18 |
| T2 | Tests live in `tests/` mirroring `nanobot/` structure | Easy to find test for any module | 2026-02-18 |
| T3 | `pytest` only — no unittest, no nose | Consistency; pytest fixtures are powerful | 2026-02-18 |
| T4 | Mock external APIs (OpenRouter, Discord, Telegram) in tests | Tests must work offline | 2026-02-18 |
| T5 | All tests must pass before commit (`python -m pytest tests/ -x -q`) | No broken builds | 2026-02-18 |
| T6 | Lab runs same AgentLoop code as production — only paths, provider, channel seams change | Eval must match prod (Anthropic eval guide) | 2026-02-19 |
| T7 | Lab state isolated per instance via `set_data_path()` + `sessions_dir` parameter | Prevents contamination, enables parallel runs | 2026-02-19 |
| T8 | RecordReplayProvider caches by hash of (model + messages + tool names), excludes temperature | Same prompt = same cache hit regardless of behavioral params | 2026-02-19 |
| T9 | Named immutable snapshots for reproducible test starting conditions | Each trial from same snapshot = identical starting state (tau-bench) | 2026-02-19 |
| T10 | State verification (tau-bench pattern) over text matching in lab tests | Text-matching evals miss 30-40% of actual failures | 2026-02-19 |

---

## Documentation

| # | Decision | Reasoning | Date |
|---|----------|-----------|------|
| D1 | `docs/CHANGELOG.md` gets an entry for every change | Audit trail | 2026-02-18 |
| D2 | `docs/architecture.html` updated when features/modules change | Visual map stays accurate | 2026-02-18 |
| D3 | `docs/WHITELIST.md` (this file) checked before every technical decision | Consistency enforcement | 2026-02-18 |
| D4 | Code comments explain WHY, not WHAT | The code shows what; comments explain the reasoning | 2026-02-18 |
| D5 | `docs/OBSERVABILITY.md` documents module events, trace_id, view_module tool, and prompt versioning | Module-level observability needs its own reference since it spans 5 modules + dashboard + tools | 2026-02-19 |
| D6 | `docs/CODING_PATTERNS.md` is the pattern reference for all Claude Code sessions — read before writing code | Prevents coding style drift across sessions; copy-paste templates enforce consistency | 2026-02-19 |

---

## Exemptions

| # | Overrides | Reason | Approved By | Date |
|---|-----------|--------|-------------|------|
| (none yet) | | | | |
