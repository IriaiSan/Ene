# Ene — Capabilities Reference

What Ene can do, how it works, and current limitations.

---

## Communication

| Capability | Status | Details |
|---|---|---|
| Discord text chat | Working | Responds in public channels when mentioned ("ene") |
| Discord DMs | Working | Responds to all DMs |
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

## Memory

| Feature | Details |
|---|---|
| Long-term memory | `MEMORY.md` — loaded into every prompt. Contains identity, people notes, rules. |
| Event history | `HISTORY.md` — append-only log, grep-searchable for past events. |
| Session history | Per-channel JSONL files. Preserves full conversation context. |
| Consolidation | Automatic when session exceeds 50 messages. LLM summarizes old messages. |
| Consolidation recovery | Retries 2x on failure, dropping 10 oldest messages each attempt. |

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
- **No per-user identity in prompts**: LLM doesn't know WHO is talking unless told by SOUL.md/MEMORY.md context. People recognition system is planned.
- **Response pattern lock**: DeepSeek v3 tends to lock into formatting patterns (numbered lists, clinical analysis). Anti-formatting rules in SOUL.md and MEMORY.md mitigate this but don't fully prevent it.
- **Single-threaded processing**: Agent loop processes one message at a time. High traffic causes queuing delays.
- **No mood system**: Planned but not yet implemented.
- **No trust scoring**: Planned but not yet implemented. Currently all non-Dad users are treated equally.
- **No sleep/wake cycle**: Planned but not yet implemented.
- **No impulse layer**: All responses currently go through LLM. Fast pre-LLM responses are planned.
- **Web search disabled**: Brave API key not configured in config.json.

## Planned (Not Yet Implemented)

These are designed and specified but not yet built:

- **ene_runtime/** — Daemon wrapper with trust, impulse, mood, and enrichment layers
- **Trust scoring** — Reputation system based on user behavior
- **Impulse layer** — Pre-LLM fast responses (reactions, greetings)
- **Mood tracker** — Float-based mood that drifts with interactions
- **People database** — Per-user profile files with trust tiers
- **Sleep/wake cycle** — Based on schedule in drives.json
- **Focus state** — "Busy with Dad" mode for public channels
- **Context-aware consolidation** — Different summarization per channel type
- **Emoji reactions** — Both automatic and LLM-requested via `[react:emoji]` tags
- **GIF responses** — Tenor API integration via `[gif:search term]` tags
