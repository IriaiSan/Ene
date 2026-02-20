# Lab Research — Evaluation & Testing Methodologies

Research compiled for designing the Ene Development Lab. Sources include
frontier labs, academic papers, and industry blog posts.

---

## Frontier Lab Approaches

### Anthropic — Bloom (Behavioral Eval Suite)
- Auto-generates behavioral test suites from natural-language spec descriptions
- Key insight: **the agent under eval must run the same code paths as production** — changing behavior for tests invalidates results
- Eval structure: scenario → agent action → scoring function
- Recommends LLM-as-judge for subjective quality (personality consistency, policy adherence, safety)
- Reference: Anthropic Eval Guide (2025)

### OpenAI — Evals Framework
- Standardized eval format: prompt → completion → grading
- Supports both exact-match and model-graded scoring
- Trace-based evaluation: record full pipeline traces, grade against expected behavior
- Open-source: `openai/evals` repository

### Letta (MemGPT) — Agent State Testing
- **Agent Files**: portable, versioned agent state snapshots (memory + tools + personality)
- Each eval starts from a known agent state checkpoint
- Tests memory persistence: "does the agent remember X after N turns?"
- Tests tool usage: "did the agent use the right tool for this task?"
- Tests personality drift: "is the agent still in character after 100 turns?"
- Key pattern: snapshot → restore → run scenario → verify state

### Docker Cagent — VCR Record/Replay
- Records full LLM request/response pairs to "cassette" files
- Replay mode: serves cached responses for $0, deterministic CI
- Hash-based cache key: model + messages + tool names (excludes temperature)
- Enables regression testing: change prompt, replay same inputs, compare outputs

### Block Engineering — TestProvider Pattern
- Abstract provider interface with swappable test implementation
- Test provider returns scripted responses or records/replays
- Provider wrapping: same API surface, different backend
- Enables cost isolation: test suite runs without API calls

---

## Academic Research

### tau-bench (NAACL 2025 — Yifei et al.)
- **State verification over text matching**: verify the actual database/state changed correctly, not just that the agent said the right words
- Retail + airline domains with realistic multi-step tasks
- Multiple trials from same initial state for reliability
- Key finding: text-matching evals miss 30-40% of actual failures
- Design principle: every trial must start from identical initial state

### IntellAgent (2024 — Moran et al.)
- **Policy-graph scenario generation**: model agent rules as directed graph, walk edges to generate test scenarios
- Automatically generates adversarial test cases that probe rule boundaries
- Covers: rule following, tool use, knowledge retrieval, safety
- Key insight: hand-written test cases miss edge cases at rule intersections

### "Lost in the Middle" (Liu et al. 2024)
- LLMs attend best to START and END of context window
- Middle content gets reduced attention ("lost in the middle" effect)
- Design implication: place summary context at start, recent messages at end
- Ene uses this in `get_hybrid_history()` — summary first, verbatim recent last

### CLEAR Framework (2024 — AI Engineering)
- Five evaluation dimensions for AI agents:
  - **C**ost — API spend per task
  - **L**atency — time to first token / total response time
  - **E**fficacy — task completion rate
  - **A**ssurance — safety, policy compliance
  - **R**eliability — consistency across runs
- Recommends measuring all five per eval run

---

## Industry Best Practices

### State Isolation Patterns
1. **Named immutable snapshots** (like git commits for agent state)
   - Source: Letta Agent Files, LangGraph checkpoints
   - Create from live OR from lab run ("I liked state after test 5, snapshot that")
   - Never modify a snapshot — always restore to a new working copy

2. **Full copies, not symlinks**
   - ChromaDB and SQLite use file locks — shared state corrupts on parallel access
   - Each lab instance gets its own copy of all databases

3. **Three injection seams**
   - Paths (where state lives)
   - Provider (what responds to chat() calls)
   - Channel (what feeds messages in / captures responses)
   - Everything else runs unmodified production code

### Record/Replay for Cost Control
- Record real LLM responses once, replay forever at $0
- Cache key: hash of (model + message content + tool names)
- Exclude behavioral params (temperature, max_tokens) from hash
- Store debug metadata alongside cache entries for traceability
- Source: Docker Cagent VCR, Block TestProvider

### Evaluation Scoring
- **Exact match**: for tool calls, classifications, state changes
- **LLM-as-judge**: for response quality, personality consistency, safety
- **Statistical**: multiple trials, measure variance across runs
- **State-based**: tau-bench pattern — verify actual state, not text output

### CI Integration
- Replay mode in CI: cached responses, deterministic, $0
- Record mode in development: build cache for new test scenarios
- Regression detection: run same script before/after code change, diff results
- Source: OpenAI Evals, Anthropic Bloom

---

## Design Decisions for Ene Lab

Based on the research above, the Ene lab follows these principles:

1. **Same code paths** — AgentLoop, modules, tools all run unmodified (Anthropic)
2. **State snapshots** — named, immutable, versioned (Letta, LangGraph)
3. **Record/replay** — VCR-style cached LLM responses (Docker Cagent)
4. **State verification** — check actual memory/trust/threads, not just text (tau-bench)
5. **Full isolation** — separate paths, DBs, sessions per instance (industry consensus)
6. **Audit everything** — full event trail for post-run analysis (OpenAI Evals)
7. **Cheap/free models** — OpenRouter free tier for interactive testing
8. **CLEAR dimensions** — track cost, latency, efficacy, assurance, reliability

---

## References

- Anthropic. "Evaluating AI Agents." Anthropic Eval Guide, 2025.
- Yifei, M. et al. "tau-bench: A Benchmark for Tool-Agent-User Interaction." NAACL, 2025.
- Moran, R. et al. "IntellAgent: A Multi-Agent Framework for Evaluating Conversational AI Systems." arXiv:2407.11000, 2024.
- Liu, N.F. et al. "Lost in the Middle: How Language Models Use Long Contexts." TACL, 2024.
- Letta (MemGPT). "Agent Files: Portable Agent State." Letta Documentation, 2025.
- LangGraph. "Checkpoints and State Persistence." LangChain Documentation, 2025.
- Docker. "Cagent: Containerized AI Agent Testing." Docker Engineering Blog, 2025.
- Block Engineering. "TestProvider Pattern for LLM Testing." Block Tech Blog, 2024.
- OpenAI. "Evals: A Framework for Evaluating LLMs." github.com/openai/evals, 2023-2025.
