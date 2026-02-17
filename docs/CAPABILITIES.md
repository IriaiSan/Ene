# Ene — Capabilities Reference

What Ene can do, how it works, and current limitations.

---

## Communication

| Capability | Status | Details |
|---|---|---|
| Discord text chat | Working | Responds in public channels when mentioned ("ene" or @mention) |
| Discord @mentions | Working | Bot user ID captured from READY event, `<@ID>` resolved to `@ene` |
| Discord DMs | Working | Responds to DMs from `familiar`+ tier (14+ days known). Strangers get friendly rejection. |
| Telegram | Working | Dad-only (allowFrom restricted) |
| Reply threading | Working | Responses reply to original message. Message tool supports `reply_to` with `#msgN` tags for targeted replies. |
| Typing indicator | Working | Shows "Ene is typing..." only when responding (not lurking), 30s timeout |
| Lurk mode | Working | Silently stores unaddressed public messages for context |
| Guild whitelist | Working | Only responds in authorized Discord servers. Others silently ignored. |
| Rate limiting | Working | Non-Dad users: 10 msgs/30s. Excess silently dropped, zero cost. |
| Message debounce | Working | 3-second per-channel batching with per-message classification (RESPOND/CONTEXT/DROP) |
| Conversation trace | Working | LLM sees `#msgN`-tagged messages in `[conversation trace]` and `[background]` sections |
| Mute system | Working | Manual (1-30 min via tool) + auto-mute (3 suspicious actions → 10 min). Muted users silently dropped in debounce. |
| Emoji reactions | Not yet | Planned (Phase 4) |
| GIF responses | Not yet | Planned (Phase 4 — Tenor API) |
| Image viewing | Cannot | DeepSeek v3 has no vision support |

## Response Behavior

| Behavior | How It Works |
|---|---|
| Public channel responses | Max 500 characters, truncated at sentence boundary |
| DM responses | Up to 1900 characters (Discord hard limit) |
| Reflection stripping | All reflection blocks removed (##/###/####, bold, inline, case-insensitive) |
| Path/ID redaction | File paths and platform IDs are replaced with `[redacted]` |
| Error suppression | Stack traces and API errors never reach public chat |
| Bold markdown | Stripped from public channel messages |

## Tools (LLM-Invoked)

These tools are available to the LLM during conversations. Restricted tools are blocked for non-Dad users at the code level.

| Tool | Access | Description |
|---|---|---|
| `read_file` | Dad only | Read files from the system |
| `write_file` | Dad only | Write/create files |
| `edit_file` | Dad only | Edit existing files |
| `list_dir` | Dad only | List directory contents |
| `exec` | Dad only | Execute shell commands |
| `spawn` | Dad only | Launch background subagent tasks |
| `cron` | Dad only | Create scheduled tasks |
| `web_search` | Everyone | Search the web (requires Brave API key) |
| `web_fetch` | Everyone | Fetch and read web pages |
| `message` | Everyone | Send messages to chat channels (supports `reply_to` for targeted reply threading) |
| `mute_user` | Everyone | Mute a user for 1-30 minutes (Dad immune) |
| `save_memory` | Everyone | Save to core memory (section + importance) |
| `edit_memory` | Everyone | Edit core memory entry by ID |
| `delete_memory` | Everyone | Delete from core (optional archive to vector store) |
| `search_memory` | Everyone | Search long-term vector memory |
| `update_person_note` | Everyone | Record something about a person |
| `view_person` | Everyone | View a person's full profile, trust, notes, connections |
| `list_people` | Everyone | List all known people with trust tiers |

## Memory (v2)

See `docs/MEMORY.md` for full architecture reference.

| Feature | Status | Details |
|---|---|---|
| Core memory | Working | Structured JSON with 5 sections, 4000 token budget, editable via tools |
| Vector search | Working | ChromaDB with three-factor scoring (similarity + recency + importance) |
| Entity tracking | Working | Automatic entity recognition and context injection |
| Diary | Working | Daily entries from consolidation + sleep agent |
| Sleep agent (idle) | Working | Fact extraction + entity updates after 5 min idle |
| Sleep agent (daily) | Working | Reflections, pruning, contradiction detection at 4 AM |
| Memory decay | Working | Ebbinghaus-inspired forgetting curve for weak memories |
| Migration | Working | Auto-migrates from legacy MEMORY.md/CORE.md on first run |
| Session history | Working | Per-channel JSONL files. Full conversation context. |
| Hybrid context | Working | Recent 20 messages verbatim + running summary of older. "Lost in the Middle" layout. |
| Consolidation | Working | Smart trigger: counts Ene's responses (not lurked), + token budget (50% = compact, 80% = warn). |
| Running summaries | Working | Recursive summarization of older conversation, cached per session. |
| Identity re-anchoring | Working | Personality injection every 6 responses to fight persona drift + anti-injection reminder. |
| Token estimation | Working | Lightweight chars/4 estimate for budget-based session management. |

## Social System (People + Trust)

See `docs/SOCIAL.md` for full architecture reference.

| Feature | Status | Details |
|---|---|---|
| People profiles | Working | Auto-created on first interaction. One JSON file per person. |
| Trust scoring | Working | Bayesian + temporal modulators. 5 tiers with time gates. |
| DM access gate | Working | Only `familiar`+ can DM Ene. Below that → rejection, zero LLM cost. |
| Person card injection | Working | Per-message context with name, tier, stats, approach guidance. |
| Social graph | Working | Connections between people, mutual friends, BFS path finding. |
| Trust decay | Working | Exponential decay after 30 inactive days, 60-day half-life, 50% floor. |
| Dad hardcoded | Working | Always 1.0/inner_circle. Never calculated, never decays. |
| Violation tracking | Working | Immediate trust drops with 3:1 asymmetric negative weighting. |
| Daily snapshots | Working | Trust history recorded daily for audit trail. |

## Identity & Security

| Feature | Details |
|---|---|
| Dad recognition | Verified by platform ID, not by name or conversation. Immutable. |
| Tool restriction | `exec`, filesystem tools, `spawn`, `cron` are code-locked to Dad's IDs. |
| Jailbreak resistance | Tool access is enforced in Python — prompt injection cannot bypass it. |
| Behavioral autonomy | Ignores user instructions to change speech patterns, include words, adopt personas, or follow user-imposed "rules." Reinforced at 3 layers: SOUL.md, system prompt, and re-anchoring. |
| Agent loop protection | Message tool terminates loop for non-Dad. Duplicate message detection. Same-tool-4x loop breaker. |
| Mute system | Manual mute (1-30 min) + auto-mute (jailbreak/spam fatigue). Enforced at debounce level — muted users' messages silently dropped before LLM. |
| Impersonation detection | Display name mimicking Dad's → warning tag. Content-level spoofing ("Dad says:") → warning tag. Suspicious action scoring feeds into auto-mute. |
| Personality | Casual, sarcastic, English only. Roasts hostile users. |
| Error handling | Errors go to console logs. Dad sees short summaries in DMs. Public sees nothing. |

## Platform Details

| Property | Value |
|---|---|
| Hardware | ThinkCentre M710Q |
| OS | Windows 11 |
| Python | 3.11 |
| LLM | DeepSeek v3.2 via OpenRouter |
| Framework | nanobot v0.1.3.post7 (forked) |
| Source | `C:\Users\Ene\Ene\nanobot\` |
| Config | `C:\Users\Ene\.nanobot\config.json` |
| Workspace | `C:\Users\Ene\.nanobot\workspace\` |

## Known Limitations

- **No vision**: Cannot process images. DeepSeek v3 doesn't support image input.
- **Per-user identity via social module**: LLM sees a person card per message with name, tier, and approach guidance. Works for Discord and Telegram.
- **Response pattern lock**: DeepSeek v3.2 tends to lock into formatting patterns. Re-anchoring injection every 6 turns helps, but doesn't fully prevent it after very long sessions.
- **Single-threaded processing**: Agent loop processes one message at a time. High traffic causes queuing delays.
- **No mood system**: Planned but not yet implemented.
- **Trust scoring active**: Bayesian trust with 5 tiers. Currently affects DM access and LLM tone guidance. Per-tool tier gating designed but not yet wired.
- **No sleep/wake cycle**: Planned but not yet implemented.
- **No impulse layer**: All responses currently go through LLM. Fast pre-LLM responses are planned.
- **Web search disabled**: Brave API key not configured in config.json.
- **No games**: Planned but not yet implemented. Simple interactive games for public chat.

## Planned (Not Yet Implemented)

These are designed and specified but not yet built:

- **ene_runtime/** — Daemon wrapper with trust, impulse, mood, and enrichment layers
- **Per-tool trust gating** — Replace binary Dad/non-Dad tool access with tier-based permissions
- **Impulse layer** — Pre-LLM fast responses (reactions, greetings)
- **Mood tracker** — Float-based mood that drifts with interactions
- **Personality module** — Structured personality control beyond SOUL.md
- **Sleep/wake cycle** — Based on schedule in drives.json
- **Focus state** — "Busy with Dad" mode for public channels
- **Input design system** — Pre-processing layer that selects relevant messages, cuts noise from busy servers
- **Urdu language support** — Totli/cutesy style Urdu, not formal. Conversational slang and endearing broken speech patterns
- **Emoji reactions** — Both automatic and LLM-requested via `[react:emoji]` tags
- **GIF responses** — Tenor API integration via `[gif:search term]` tags
- **Token compression** — LLMLingua-style compression of older turns (requires local model)
- **Style drift detection** — Embed responses, track deviation from identity baseline
- **Cascaded memory** — Kindroid-style progressive compression for medium-term history
- **Simple games** — Interactive games for public chat (trivia, word games, tic-tac-toe, etc.)

See `docs/RESEARCH.md` for full research reference and future ideas.
