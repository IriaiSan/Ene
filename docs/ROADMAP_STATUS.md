# Ene Roadmap — Status as of February 22, 2026

Current state snapshot. Updated each development session.

> **v0.5 Comprehensive Implementation Plan** — See [`docs/ENE_V05_PLAN.md`](ENE_V05_PLAN.md) for the full 17-phase architecture plan (State Vector, EneCore, Memory Graph, RWKV migration, VTuber embodiment, custom model training).

---

## Phase 1 — Foundation (COMPLETE)

Everything built during Era 3.5 (Feb 16-18). All systems operational.

| System | Status | Notes |
|--------|--------|-------|
| Core agent loop | Done | `loop.py` ~1800 lines, locked |
| Security (DAD_IDS, mute, rate limit) | Done | Hardcoded trust root, 10/30s rate limit |
| Memory v2 (core + vector + sleep) | Done | 4000 token budget, ChromaDB, idle/daily paths |
| Social (trust scoring, profiles) | Done | Bayesian + temporal, 5 tiers, 21 people tracked |
| Conversation tracker (Module 5) | Done | Thread detection, lifecycle, formatter |
| Daemon pre-classifier (Module 6) | Done | Free model rotation, 5s timeout, math fallback |
| Observatory + dashboard | Done | 17+ event types, SSE stream, cost tracking |
| Module-level observability | Done | 18 event types across 5 modules, trace IDs |
| Prompt version control | Done | 9 prompts extracted to files, PromptLoader |
| Session management | Done | JSONL, hybrid history, running summaries |
| Discord + Telegram channels | Done | Gateway WS, REST API, guild whitelist |
| Lab infrastructure | Done | Snapshots, record/replay, stress test, audit |
| Documentation (14 docs) | Done | All accurate and current |

---

## Phase 2 — In Progress (ene_runtime / Daemon)

| Item | Status | Notes |
|------|--------|-------|
| Trust scoring | **Done** | Bayesian + geometric mean, anti-gaming |
| Daemon classification | **Done** | Free model rotation + math classifier fallback |
| Thread-aware context | **Done** (Feb 20) | Full dialogue in threads, #msgN tag reduction |
| Ene responses in threads | **Done** (Feb 20) | `add_ene_response()` injects Ene's replies |
| Discord RESUME + backoff | **Done** (Feb 20) | Exponential backoff, session resume on reconnect |
| Garbled XML defense | **Done** (Feb 20) | Broadened suppression, thread injection guard |
| Status dashboard (bento box) | **Done** (Feb 20) | Kanban threads, pipeline view, pending cards fixed |
| Fix duplicate response bug | **Fixed** (Feb 20) | Pre-execution guard blocks duplicate message tool calls; post-message turn limit; session marker leak fixed |
| loop.py split (<500 lines/file) | **Done** (Feb 21) | Split into 5 modules: batch_processor, message_processor, memory_consolidator, debounce_manager, state_inspector. loop.py ~1156 lines |
| Per-tool trust gating | **Not done** | Replace binary Dad/non-Dad with tier-based access |
| Impulse layer | **Not done** | Fast pre-LLM responses for common patterns |
| Mood tracker | **Not done** | Float-based mood that drifts with interactions |
| Focus state | **Not done** | "Busy with Dad" mode |
| Sleep/wake cycle | **Not done** | Based on schedule |
| Enrichment layer | **Not done** | Additional context injection before LLM |

---

## Phase 3 — Not Started (Intelligence Enhancement)

| Item | Status |
|------|--------|
| Prediction-outcome logging | Not started |
| Self-model maintenance | Not started |
| Energy/survival economics | Not started |
| Controlled randomness (synthetic urges) | Not started |
| A/B testing infrastructure | Not started |
| Training data extraction pipeline | Not started |
| Benchmark preparation | Not started |
| Multi-person debounce context | Not started |

---

## Phase 4 — Not Started (Discord Richness)

| Item | Status |
|------|--------|
| Emoji reactions | Not started |
| GIF responses (Tenor API) | Not started |
| Interactive games | Not started |
| Sticker support | Not started |

---

## Phase 5 — Not Started (Embodiment)

| Item | Status |
|------|--------|
| Open-LLM-VTuber integration | Not started |
| Desktop pet mode | Not started |
| Streaming setup | Not started |
| Social media accounts | Not started |
| Voice cloning | Not started |

---

## Phase 6 — Not Started (Independence)

| Item | Status |
|------|--------|
| Local model deployment (RWKV) | Not started |
| State persistence (soul file) | Not started |
| Echidna intelligence integration | Not started |
| Proactive behavior | Not started |
| Nightly training cycle | Not started |
| Hardware upgrade justification | Not started |

---

## Deferred Items (identified during Feb 20 session)

Things we discovered need doing but deferred for now:

| Item | Why deferred | Priority |
|------|-------------|----------|
| DeepSeek model replacement | Root cause of garbled XML; mitigated by cleaning for now | Medium — revisit when better model available on OpenRouter |
| Session history pruning of stale context entries | Core memory has ~17 stale one-time events in context section | Low — sleep agent should handle on next deep pass |
| Vector memory (ChromaDB) audit | May contain bad entries from garbled sessions | Low — no user-facing symptoms yet |
| Daemon SecurityFlag serialization bug | `sequence item 0: expected str instance, SecurityFlag found` in logs | Medium — daemon crashes back to math fallback, works but wastes time |
| Watchdog module (Module 4) | Disabled to save costs — free model rotation not battle-tested | Low — intentional |
| Web search (Brave API) | Key not configured | Low — not requested |
| `condense_for_session()` for Ene's assistant entries | **Fixed** (Feb 20c) — session now stores real message content | Done |

---

## Known Active Bugs

| Bug | Description | Severity |
|-----|-------------|----------|
| ~~Duplicate responses~~ | **Fixed** — pre-execution guard + post-message turn limit + session marker fix | Resolved |
| DeepSeek pattern lock | v3.2 locks into formatting patterns after long sessions | Medium — mitigated by re-anchoring every 6 turns |
| DNS/connectivity issues | Machine can't resolve Discord/Telegram hostnames intermittently | External — not a code bug |

---

## Session Summary — February 20, 2026

### What was done
1. Ene's responses injected into threads (full conversation tracking)
2. #msgN tags reduced to last non-Ene message only (noise reduction)
3. Garbled XML cleaning hardened (response_cleaning.py + loop.py + tracker.py)
4. Session storage fixed — stores real message content, not `[responded via message tool]`
5. `<message>` tag stripping added to `clean_response()`
6. Discord RESUME support + exponential backoff added
7. Status dashboard pending cards fixed
8. Core memory audited and corrected (helmet/majamiac fix, duplicates removed)
9. Garbled XML entries + poisoned session markers cleaned
10. Thread and session state cleared multiple times as needed
11. Duplicate response bug resolved (pre-exec guard + post-message limit + marker fix)

---

## Session Summary — February 21-22, 2026

### What was done
1. **loop.py decomposition** — Split ~1800 line monolith into 5 focused modules:
   - `batch_processor.py` (625 lines) — classify → merge → dispatch pipeline
   - `message_processor.py` (493 lines) — per-message gate → decide → respond → store
   - `memory_consolidator.py` (322 lines) — diary, running summaries, re-anchoring
   - `debounce_manager.py` (126 lines) — debounce buffer + queue processor
   - `state_inspector.py` (170 lines) — hard reset, model switch, brain toggle
   - `loop.py` reduced to ~1156 lines (init + `_run_agent_loop` + `run()` + thin delegates)
2. **Dashboard revamp** — 8-phase overhaul of live.html with new event types, state panel, prompt viewer
3. **Thread Inspector page** — New `/threads` page with split layout (message log + decision tree visualization)
4. **Model pricing fixes** — Added missing pricing for `anthropic/claude-sonnet-4`, `google/gemini-2.5-pro`
5. **Cost panel** — Added per-model cost breakdown to dashboard metrics
6. **Thread graph redesign** — Replaced kanban-style thread cards with node-based graph visualization
7. **Settings page** — New `/settings` page with:
   - Model slot management (primary, consolidation, daemon)
   - Custom LLM registration with persistent storage
   - Runtime pricing table with active/custom model indicators
   - Runtime config info grid
8. **Reply targeting fix** — Fixed two bugs preventing correct reply threading:
   - `batch_processor.py`: `_build_thread_message` now uses thread's last non-Ene message ID instead of batch trigger ID
   - `message_processor.py`: `_handle_response` now resolves reply targets via formatter's `msg_id_map` instead of raw metadata
9. **ENE v0.5 Implementation Plan** — Documented full 17-phase architecture plan in `docs/ENE_V05_PLAN.md`

### Ene status: STABLE
All systems operational. Dashboard fully functional with 6 pages (Metrics, Status, Control, Live Trace, Threads, Settings).

### Test count
1176 tests passing
