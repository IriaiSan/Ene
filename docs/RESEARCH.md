# Ene — Research Reference & Future Ideas

Research conducted 2026-02-16 to inform context window, session management, and persona design decisions. Includes industry analysis, academic papers, and frontier AI safety approaches. Items marked **[IMPLEMENTED]** are in the codebase; everything else is documented for future reference.

---

## Industry Analysis

### Character.AI
- Tiny ~4K context window, 95% KV cache hit rate
- Aggressive summarization: only keeps personality card + last few turns + compressed memory
- Lesson: Even tiny windows work if summarization is good enough

### Kindroid (Three-Tier Memory)
- **Persistent memory**: Always-on, identity/preferences (like our core memory)
- **Cascaded memory**: Medium-term progressive compression (messages → summaries → meta-summaries)
- **Retrievable memory**: Long-term keyphrase-triggered recall (like our vector search)
- Lesson: The cascaded tier is the most interesting — progressive compression maintains context quality while reducing tokens

### ChatGPT (Reverse-Engineered)
- Surprisingly simple: no RAG, no vector DB in the memory feature
- Pre-computed summaries + fact store injected every prompt
- Memory is explicitly managed by user ("Remember that I prefer X")
- Lesson: Simple approaches work at scale. Don't over-engineer early.

### JanitorAI / Replika / Chai AI
- User-managed summaries (JanitorAI)
- Replika uses "memory entries" — discrete facts, not narrative summaries
- Chai AI: minimal context, relies on personality card + last 3-5 turns
- Lesson: For chat companions, personality consistency matters more than total recall

---

## Academic Papers

### MemGPT / Letta (Packer et al. 2023) **[PARTIALLY IMPLEMENTED]**
- Context window = RAM, external storage = disk
- LLM self-manages memory via tools (our `save_memory` pattern)
- Recursive compaction: when context fills up, summarize older turns into shorter form
- **What we implemented**: save_memory tool, core memory concept, running summaries
- **What we didn't**: Full LLM-driven memory management (self-compaction, page in/out). Currently our summarization is system-driven, not LLM-initiated.

### "Lost in the Middle" (Liu et al. 2024) **[IMPLEMENTED]**
- LLMs attend best to START and END of context, ignore the middle
- Performance drops 20-30% for information placed in the middle third
- **What we implemented**: System prompt at top, summaries in early-middle, recent messages at end, re-anchoring near end

### Recursive Summarization (Wang et al. 2023) **[IMPLEMENTED]**
- Summarize chunks, then summarize the summaries
- Maintains quality better than one-shot summarization of large texts
- **What we implemented**: Running summaries that incorporate previous summary + new messages

### StreamingLLM (Xiao et al. 2023)
- "Attention sinks" — first few tokens get disproportionate attention regardless of content
- Sliding window with principled beginning-token retention
- **Not implemented**: Requires model-level changes. But validates our approach of keeping system prompt (first tokens) stable.
- **Future use**: If we switch to a locally-hosted model, could implement attention sink preservation.

### LLMLingua / LongLLMLingua (Jiang et al. 2023)
- Token-level compression for API-based LLMs
- Identifies and removes "unimportant" tokens from prompts
- 2-5x compression with minimal quality loss
- **Not implemented**: Requires a small local model to run the compression. Could be valuable for reducing API costs on older conversation turns.
- **Future use**: Run LLMLingua on older messages before injecting as summary. Reduces tokens while preserving key information.

### Infini-attention (Google, Munkhdalai et al. 2024)
- Compressive memory integrated directly into attention layers
- Theoretically infinite context by compressing old KV cache into fixed-size memory
- **Not implemented**: Model architecture change, not applicable to API-based usage
- **Future relevance**: If/when models ship with this built-in, our explicit summarization may become less critical

### Ring Attention (Liu et al. 2024)
- Distributed multi-GPU technique for very long contexts
- **Not applicable**: Ene runs on a single ThinkCentre. Academic interest only.

### ICAE / 500xCompressor
- Compressed memory tokens (500 tokens → 1 token representation)
- Requires model fine-tuning to understand compressed representations
- **Not implemented**: Can't fine-tune DeepSeek v3.2 via API
- **Future relevance**: If we move to local model, could dramatically reduce context usage

### A-MEM: Agentic Memory (Xu et al. 2025)
- Zettelkasten-style structured notes with dynamic links
- Each memory is a "note" with: title, content, tags, links to other notes
- LLM decides: add, update, link, or ignore new information
- **Not implemented**: Our core memory is simpler (flat entries by section)
- **Future use**: Could evolve core memory into linked note format for richer recall

### Mem0 (Chhikara et al. 2024)
- Four operations: add, merge, update, skip
- Graph variant for tracking relationships between memories
- Conflict resolution when new info contradicts existing
- **Partially implemented**: Our save/edit/delete + vector search covers similar ground
- **Future use**: The "merge" operation (combining related memories) would help core memory stay compact

### DH-RAG: Dynamic History RAG
- RAG specifically for multi-turn dialogue
- Retrieves relevant past conversation segments, not just individual facts
- **Not implemented**: We use vector search on memories, not on raw conversation
- **Future use**: Could search past sessions for relevant conversation fragments

---

## Context Engineering Best Practices

### Token Budgeting **[IMPLEMENTED]**
- System prompt: ~750 tokens (keep stable for KV cache)
- Reserve ~25% for output (max_tokens)
- Begin compaction at 50% utilization
- Hard compact at 80%
- **What we implemented**: 60K token budget for history, 50% = begin summarization, 80% = warning

### Persona Drift **[IMPLEMENTED]**
- 30%+ degradation after 8-12 turns (measured across multiple models)
- Worse with larger models and longer contexts
- Fix: periodic identity re-injection near current message (high-attention zone)
- **What we implemented**: Re-anchoring every 10 assistant messages

### "Smart Zone" Positioning **[IMPLEMENTED]**
- First ~40% of context is where LLM performs best
- System prompt + identity should be in this zone
- Older/less important info goes in middle
- Current task goes at the end
- **What we implemented**: System prompt at top, summaries in middle, recent + re-anchor at end

### DeepSeek v3.2 Specific Quirks
- 128K context window, sparse attention
- Documented "lost in the middle" effect
- Silent truncation near context limit (no error, just stops reading)
- Strong stylistic drift in long conversations (more than other models)
- Tends toward numbered lists and clinical formatting
- **Mitigated by**: re-anchoring, compact summaries, SOUL.md anti-formatting

---

## Future Ideas (Not Yet Designed)

### Input Design System
**Idea**: Instead of giving Ene everything, only give her the messages she needs to reply to. Cut out noise, lurked messages, irrelevant banter. Design a pre-processing layer that selects and formats input for maximum relevance.
- Could use a small/fast model to classify message relevance
- Could use heuristics (mentioned Ene, replied to Ene, direct question, etc.)
- Would dramatically reduce token usage in busy servers
- Complementary to hybrid context (input design = what goes in, context window = how it's arranged)

### Urdu Language Support — Totli/Cutesy Style
**Idea**: Implement Urdu language capability for Ene, specifically:
- NOT generic/formal Urdu
- Normal conversational slang
- "Totli" (cutesy/baby-talk) style — how kids speak
- Broken but endearing Urdu that makes sense contextually
- Specific word substitutions and speech patterns
- Could be a personality module or skill that activates based on language detection
- Would need curated examples in SOUL.md or a dedicated language skill file

### EchoMode Behavioral State Machines
- Define personality as a state machine with transitions
- States: playful, focused, reflective, sarcastic, etc.
- Transitions triggered by conversation patterns
- Would give Ene more natural emotional flow

### SyncScore / Style Embedding Drift Detection
- Embed Ene's responses and track drift from baseline
- Alert when style deviates too far from identity
- Auto-trigger stronger re-anchoring when drift detected
- Requires embedding Ene's responses and comparing to reference set

### Kindroid-Style Cascaded Memory
- Implement the three-tier model properly:
  - Tier 1: Core memory (always loaded) — already have
  - Tier 2: Cascaded (progressive compression with configurable granularity)
  - Tier 3: Retrievable (keyphrase-triggered from vector store) — already have
- The missing piece is Tier 2: systematic compression of medium-term history

### Per-Tool Trust Gating
- Currently: binary Dad/non-Dad tool access
- Designed in social module but not wired: tier-based permissions per tool
- e.g., `trusted` can use `web_search`, `inner_circle` can use `exec`
- Would need a tool-tier mapping config

### Token-Level Compression (LLMLingua)
- Use a small local model to compress older conversation turns
- Preserve key information while reducing token count 2-5x
- Would require running a small model locally (e.g., Phi-2)
- Best ROI for reducing API costs on long conversations

### Message Debouncing & Chat-Level Processing **[PLANNED]**
- **Problem**: People send messages in bursts (7 lines = 7 separate messages). Ene responds to each one individually, missing context and wasting tokens.
- **Also**: Multiple people talking to Ene get separate responses instead of one contextual reply.
- **Solution**: Per-channel debounce window (2-3 seconds). Collect all incoming messages, batch them, process as one prompt.
- **Format**: Group messages with author labels so Ene sees the full conversation chunk.

### Sandbox Tools for Ene (Safe Autonomy)
- **Problem**: Currently Ene's tools are blocked for everyone except Dad. But blocking is temporary — want to give her freedom to actually do things.
- **Solution**: Sandboxed execution environments. Ene can create containers/sandboxes to experiment, code, create content — without risk to the host system.
- **Examples**: Run code in a sandbox, create files in a temp space, browse the web safely.
- **Ties into**: Phase 2 (ene_runtime) and Phase 3 (trust-gated tool access).

### Impulse/Urge System (Subconscious Layer)
- **Concept**: A small/free model that reads everything and decides if Ene should act. Operates on totally different instructions. The "cold calculating higher-level subconscious."
- **How**: Small model (e.g., Phi-3, Qwen-2, or free-tier model) as a fast classifier:
  - New message → should Ene respond, lurk, or react-only?
  - Server been quiet → should Ene initiate something?
  - Ene been idle → should she do something proactive?
- **Key design**: Ene does NOT have access to or awareness of this layer. Just like human impulses — you can kinda explain why you did something, but the actual trigger mechanism is opaque.
- **Meta-awareness**: How much can Ene realize about herself? Models already detect when they're being tested and adjust behavior. We can leverage this for naturalistic self-awareness without full transparency of the impulse layer.
- **Ties into**: Phase 3 (Trust/Impulse/Mood system), Input Design System.

### Discord Reactions, Emojis & GIF Collection
- **Problem**: Ene can only send text. No reactions, no emojis in responses, no GIFs.
- **Solution**:
  - Add Discord reaction API support (add_reaction endpoint).
  - Give Ene a GIF search tool (Tenor/GIPHY API).
  - Let her curate her own collection of favorites over time.
  - Hunt for Ene (anime character) GIFs specifically.
  - Possibly create custom stickers/emojis.
- **Ties into**: Phase 4 (Discord richness), Impulse system (react-only decisions).

---

*Last updated: 2026-02-16*
