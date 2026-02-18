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

---

## Testing

| # | Decision | Reasoning | Date |
|---|----------|-----------|------|
| T1 | All new code must have tests | No untested code ships | 2026-02-18 |
| T2 | Tests live in `tests/` mirroring `nanobot/` structure | Easy to find test for any module | 2026-02-18 |
| T3 | `pytest` only — no unittest, no nose | Consistency; pytest fixtures are powerful | 2026-02-18 |
| T4 | Mock external APIs (OpenRouter, Discord, Telegram) in tests | Tests must work offline | 2026-02-18 |
| T5 | All tests must pass before commit (`python -m pytest tests/ -x -q`) | No broken builds | 2026-02-18 |

---

## Documentation

| # | Decision | Reasoning | Date |
|---|----------|-----------|------|
| D1 | `docs/CHANGELOG.md` gets an entry for every change | Audit trail | 2026-02-18 |
| D2 | `docs/architecture.html` updated when features/modules change | Visual map stays accurate | 2026-02-18 |
| D3 | `docs/WHITELIST.md` (this file) checked before every technical decision | Consistency enforcement | 2026-02-18 |
| D4 | Code comments explain WHY, not WHAT | The code shows what; comments explain the reasoning | 2026-02-18 |

---

## Exemptions

| # | Overrides | Reason | Approved By | Date |
|---|-----------|--------|-------------|------|
| (none yet) | | | | |
