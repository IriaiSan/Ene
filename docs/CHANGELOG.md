# Ene Growth Changelog

All notable changes to Ene's systems, behavior, and capabilities.

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
