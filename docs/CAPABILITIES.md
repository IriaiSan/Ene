# Ene — Capabilities Reference

What Ene can do, how it works, and current limitations.

---

## Communication

| Capability | Status | Details |
|---|---|---|
| Discord text chat | Working | Responds in public channels when mentioned ("ene") |
| Discord DMs | Working | Responds to DMs from `familiar`+ tier (14+ days known). Strangers get friendly rejection. |
| Telegram | Working | Dad-only (allowFrom restricted) |
| Reply threading | Working | Responses reply to the original message |
| Typing indicator | Working | Shows "Ene is typing..." during LLM processing |
| Lurk mode | Working | Silently stores unaddressed public messages for context |
| Emoji reactions | Not yet | Planned (Phase 4) |
| GIF responses | Not yet | Planned (Phase 4 — Tenor API) |
| Image viewing | Cannot | DeepSeek v3 has no vision support |

## Response Behavior

| Behavior | How It Works |
|---|---|
| Public channel responses | Max 500 characters, truncated at sentence boundary |
| DM responses | Up to 1900 characters (Discord hard limit) |
| Reflection stripping | `## Reflection` blocks are removed before sending |
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
| `message` | Everyone | Send messages to chat channels |
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
| Consolidation | Working | Diary entry writing when session exceeds 50 messages. |

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
| Personality | Casual, sarcastic, bilingual (English/Urdu). Roasts hostile users. |
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
- **No @mention detection**: Responds to text "ene", not Discord @mentions.
- **Per-user identity via social module**: LLM sees a person card per message with name, tier, and approach guidance. Works for Discord and Telegram.
- **Response pattern lock**: DeepSeek v3 tends to lock into formatting patterns (numbered lists, clinical analysis). Anti-formatting rules in SOUL.md and MEMORY.md mitigate this but don't fully prevent it.
- **Single-threaded processing**: Agent loop processes one message at a time. High traffic causes queuing delays.
- **No mood system**: Planned but not yet implemented.
- **Trust scoring active**: Bayesian trust with 5 tiers. Currently affects DM access and LLM tone guidance. Per-tool tier gating designed but not yet wired.
- **No sleep/wake cycle**: Planned but not yet implemented.
- **No impulse layer**: All responses currently go through LLM. Fast pre-LLM responses are planned.
- **Web search disabled**: Brave API key not configured in config.json.

## Planned (Not Yet Implemented)

These are designed and specified but not yet built:

- **ene_runtime/** — Daemon wrapper with trust, impulse, mood, and enrichment layers
- **Per-tool trust gating** — Replace binary Dad/non-Dad tool access with tier-based permissions
- **Impulse layer** — Pre-LLM fast responses (reactions, greetings)
- **Mood tracker** — Float-based mood that drifts with interactions
- **Personality module** — Structured personality control beyond SOUL.md
- **Sleep/wake cycle** — Based on schedule in drives.json
- **Focus state** — "Busy with Dad" mode for public channels
- **Context-aware consolidation** — Different summarization per channel type
- **Emoji reactions** — Both automatic and LLM-requested via `[react:emoji]` tags
- **GIF responses** — Tenor API integration via `[gif:search term]` tags
