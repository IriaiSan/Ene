# Ene -- Module-Level Observability

Per-module structured event recording. Answers "WHY did Ene do that?" by capturing decisions across the pipeline with full trace linkage.

---

## Overview

The observatory's LLM call tracking (cost, tokens, latency) tells you WHAT happened. Module-level observability tells you WHY -- which threads were created, how messages were classified, whether the daemon timed out, what the cleaning pass removed, and what the sleep agent extracted from memory.

Every instrumented module emits structured events through `ModuleMetrics`. Events go to two places simultaneously:
1. **SQLite** (`module_events` table) -- persistent, queryable, used by `view_module` tool and dashboard API
2. **LiveTracer** (SSE) -- real-time, ephemeral, used by the live dashboard at `localhost:18791/live`

A `trace_id` links all events from a single message batch across every module, so you can trace one message from classification through thread assignment through LLM response through cleaning.

## Architecture

### ModuleMetrics class

File: `nanobot/ene/observatory/module_metrics.py`

Each module gets its own `ModuleMetrics` instance scoped to a module name. Instances are created in `loop.py` during `_register_ene_modules()` and wired to modules via `set_metrics()`.

```python
# Created in loop.py
from nanobot.ene.observatory.module_metrics import ModuleMetrics

signals_metrics = ModuleMetrics("signals", store, self._live)
signals_mod.set_metrics(signals_metrics)
```

Two recording methods:

```python
# Fire-and-forget event
metrics.record("thread_created", channel_key, thread_id="abc", method="reply_to")

# Paired span with automatic duration tracking
with metrics.span("context_built", channel_key) as span_data:
    result = build_context()
    span_data["active_threads"] = len(result.active)
```

`NullModuleMetrics` is a silent no-op subclass used when the observatory is disabled. Modules don't need `if self._metrics:` guards when using it (though most do guard anyway for the case where metrics is literally None).

### module_events SQLite table

```sql
CREATE TABLE IF NOT EXISTS module_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp       TEXT NOT NULL,
    trace_id        TEXT,
    module          TEXT NOT NULL,
    event_type      TEXT NOT NULL,
    channel_key     TEXT DEFAULT '',
    data            TEXT DEFAULT '{}',     -- JSON payload
    duration_ms     INTEGER
);

-- Indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_module_events_ts ON module_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_module_events_module ON module_events(module, event_type);
CREATE INDEX IF NOT EXISTS idx_module_events_trace ON module_events(trace_id);
```

### Dual output path

When `ModuleMetrics.record()` is called:

1. **SQLite**: Calls `store.record_module_event()` with timestamp, module, event_type, channel_key, data (JSON), duration_ms, trace_id
2. **LiveTracer**: Calls `tracer.emit()` with event type `mod_{module}_{event_type}` (e.g., `mod_tracker_thread_created`), injecting module name and trace_id into the payload

Both paths are failure-safe -- exceptions are logged at debug level, never raised.

### Wiring

Five modules are instrumented. All wiring happens in `loop.py` `_register_ene_modules()`:

| Module | Metrics instance | Attached via |
|--------|-----------------|--------------|
| signals | `ModuleMetrics("signals", store, live)` | `signals_mod.set_metrics()` |
| tracker | `ModuleMetrics("tracker", store, live)` | `tracker_mod.set_metrics()` |
| daemon | `ModuleMetrics("daemon", store, live)` | `daemon_proc_mod.set_metrics()` |
| cleaning | `ModuleMetrics("cleaning", store, live)` | `cleaning_mod.set_metrics()` |
| memory | `ModuleMetrics("memory", store, live)` | `sleep_agent_mod.set_metrics()` |

References are stored in `self._module_metrics` dict for trace_id propagation.

---

## Trace ID

Generated at the start of `_process_batch()` in `loop.py`:

```python
self._batch_counter += 1
trace_id = f"{int(time.time())}_{channel_key}_{self._batch_counter}"
for metrics in self._module_metrics.values():
    metrics.set_trace_id(trace_id)
```

Format: `{unix_timestamp}_{channel_key}_{batch_counter}` (e.g., `1708300000_discord:123_42`).

Every module event recorded during that batch gets the same trace_id. This lets you reconstruct the full processing timeline for a single message batch across all modules: daemon classification, signal scoring, thread assignment, context build, cleaning pass.

---

## Modules and Their Events

### tracker (6 events)

File: `nanobot/ene/conversation/tracker.py`

| Event | When | Key data fields |
|-------|------|----------------|
| `thread_created` | New thread promoted from pending | `thread_id`, `channel_key`, `method` |
| `thread_assigned` | Message assigned to a thread | `thread_id`, `method` (reply_to / scoring / pending_promote), `author` |
| `thread_state_change` | Thread transitions ACTIVE->STALE or STALE->DEAD | `thread_id`, `old_state`, `new_state`, `lifespan_ms`, `message_count` |
| `pending_promoted` | Pending message group promoted to full thread | `thread_id`, `messages_accumulated` |
| `ene_involved` | Thread marked as Ene-involved after response | `thread_id` |
| `context_built` | Thread context formatted for LLM (uses `span()`) | `active_threads`, `background_threads`, `total_messages`, `duration_ms` |

`context_built` is recorded via `metrics.span()`, so it includes automatic duration tracking.

### signals (2 events)

File: `nanobot/ene/conversation/signals.py`

| Event | When | Key data fields |
|-------|------|----------------|
| `scored` | Math classifier scores a message | `sender`, `result` (respond/context/drop), `confidence`, `features`, `raw_log_odds` |
| `override` | Hard override changes classification | (Counted in stats; currently emitted via LiveTracer in loop.py, not yet through ModuleMetrics) |

The `features` dict in `scored` events contains the raw feature values used in scoring:

- `mention` -- @ene mention present
- `reply_to_ene` -- reply to an Ene message
- `temporal` -- recency weight
- `author_history` -- past interaction frequency
- `conversation_state` -- active thread context

These are the inputs to the logistic scoring formula. Useful for debugging why a message was classified as CONTEXT when it should have been RESPOND.

### daemon (3 events)

File: `nanobot/ene/daemon/processor.py`

| Event | When | Key data fields |
|-------|------|----------------|
| `classified` | Free model successfully classified a message | `model_used`, `classification`, `confidence`, `topic`, `tone` |
| `timeout` | Daemon exceeded 5s timeout | `model_attempted`, `fallback_to` (math_classifier or regex) |
| `model_rotation` | Daemon rotated to next free model after failure | `from_model`, `to_model`, `reason` |

### cleaning (1 event)

File: `nanobot/agent/response_cleaning.py`

| Event | When | Key data fields |
|-------|------|----------------|
| `cleaned` | Response passed through `clean_response()` | `raw_length`, `clean_length`, `chars_removed`, `truncated`, `truncation_point`, `was_blocked`, `is_public` |

`was_blocked=True` means the response was reduced to empty after cleaning (all content was stripped). `truncated=True` means it hit the 1900 char Discord limit.

### memory (6 events)

File: `nanobot/ene/memory/sleep_agent.py`

| Event | When | Key data fields |
|-------|------|----------------|
| `facts_extracted` | Quick path extracted facts from idle conversation | `count`, `entity_count`, `source` |
| `diary_written` | Diary entry written during idle/daily processing | `entry_length`, `facts_count` |
| `reflection_generated` | Deep path generated a reflection | `topic`, `insight_length` |
| `contradiction_found` | New fact contradicts existing memory | `existing_memory`, `new_fact`, `resolution` (keep existing or replace) |
| `pruning_decision` | Deep path pruning reviewed low-value memories | `candidates_reviewed`, `items_pruned`, `items_kept` |
| `budget_check` | Core memory budget checked during processing | `current_tokens`, `max_tokens`, `utilization_pct` |

---

## Prompt Versioning

File: `nanobot/agent/prompts/loader.py`

All prompt templates are stored as `.txt` files in `nanobot/agent/prompts/` with a `manifest.json` tracking metadata.

### manifest.json

```json
{
    "version": "1.0.0",
    "updated": "2026-02-19",
    "prompts": {
        "daemon_system": {
            "file": "daemon_system.txt",
            "source": "nanobot/ene/daemon/processor.py",
            "description": "Security daemon pre-classifier system prompt",
            "variables": []
        },
        "summary_update": {
            "file": "summary_update.txt",
            "source": "nanobot/agent/loop.py",
            "description": "Update existing running summary with new messages",
            "variables": ["existing_summary", "older_text"]
        }
    }
}
```

### PromptLoader

```python
loader = PromptLoader()

# Load with variable substitution
prompt = loader.load("summary_update", existing_summary="...", older_text="...")

# Load raw template
raw = loader.load_raw("daemon_system")

# Version string from manifest
print(loader.version)  # "1.0.0"

# List all registered prompts
print(loader.list_prompts())  # ["daemon_system", "summary_system", ...]

# Clear cache (forces re-read from disk)
loader.reload()
```

Missing template variables don't crash -- `_SafeDict` returns `{name}` for unsubstituted vars.

The `view_module` tool and `/api/live/modules` endpoint both report the current prompt version.

### Prompt files

| File | Used by | Variables |
|------|---------|-----------|
| `daemon_system.txt` | Daemon pre-classifier | none |
| `summary_system.txt` | Running summary builder | none |
| `summary_update.txt` | Update existing summary | `existing_summary`, `older_text` |
| `summary_new.txt` | Create new summary | `older_text` |
| `diary_system.txt` | Diary consolidation | none |
| `diary_user.txt` | Diary user prompt | `roster`, `conversation` |
| `diary_fallback.txt` | Diary fallback (no roster) | `conversation` |
| `identity_dad.txt` | Identity block for Dad | `now`, `tz`, `runtime`, `workspace_path` |
| `identity_public.txt` | Identity block for non-Dad | `now`, `tz`, `mute_context` |

---

## Using the view_module Tool

Dad-only tool (restricted via DAD_IDS in loop.py). Available as `view_module` in Ene's tool registry.

### Parameters

| Param | Type | Description |
|-------|------|-------------|
| `module` | string (optional) | `tracker`, `signals`, `daemon`, `memory`, `cleaning`. Omit for overview. |
| `trace_id` | string (optional) | Trace a single message batch across all modules. |
| `hours` | int (optional) | Time window, default 24, max 168. |

### Example: Overview

```
> view_module
Module Health Overview (last 24h):

tracker: 47 threads created, 312 assignments, avg 6.6 msg/thread
signals: 312 classified -- R:89 C:198 D:25 (avg confidence: 0.72)
daemon: 89 success, 3 timeouts
cleaning: 89 responses cleaned
memory: 12 extractions, 2 reflections
prompts: v1.0.0
```

### Example: Tracker detail

```
> view_module module=tracker hours=6
Module: tracker (last 6h)

Threads created: 12
Total assignments: 87
Assignment methods:
  reply_to: 34
  scoring: 41
  pending_promote: 12
Avg lifespan: 482.3s
Avg messages/thread: 7.2
Avg active threads: 3.1
Avg background threads: 1.4

Recent events:
  [14:32:07] thread_assigned method=reply_to thread_id=t_abc123
  [14:31:55] context_built active_threads=3 duration_ms=12
  [14:31:42] thread_created thread_id=t_abc123 method=reply_to
```

### Example: Signals detail

```
> view_module module=signals
Module: signals (last 24h)

Total: 312
Distribution:
  RESPOND: 89 (29%)
  CONTEXT: 198 (63%)
  DROP: 25 (8%)
Avg confidence: 0.723
Feature averages:
  mention: 0.29
  reply_to_ene: 0.18
  temporal: 0.65
  author_history: 0.42
  conversation_state: 0.31
Overrides: 7
```

### Example: Trace ID lookup

```
> view_module trace_id=1708300000_discord:123_42
Trace: 1708300000_discord:123_42 (8 events)

  [14:31:40] daemon/classified (230ms) | model=glm-4-9b-chat, classification=respond
  [14:31:40] signals/scored | result=respond, confidence=0.87
  [14:31:40] tracker/thread_assigned | method=reply_to, thread_id=t_abc123
  [14:31:41] tracker/context_built (12ms) | active_threads=3, total_messages=18
  [14:31:43] cleaning/cleaned | raw_length=342, clean_length=310, truncated=False
```

This is the primary debugging workflow: find a suspicious response in the live dashboard, grab its trace_id, and see every decision that led to it.

---

## Dashboard

### Module Health panel

The live dashboard at `localhost:18791/live` includes a Module Health panel that shows aggregated stats for all instrumented modules.

### /api/live/modules endpoint

```
GET /api/live/modules?hours=1
```

Returns JSON with per-module health:

```json
{
    "tracker": {
        "threads_created": 12,
        "total_assignments": 87,
        "assignment_methods": {"reply_to": 34, "scoring": 41, "pending_promote": 12},
        "avg_lifespan_ms": 482300,
        "avg_messages_per_thread": 7.2,
        "avg_active_threads": 3.1,
        "avg_background_threads": 1.4
    },
    "signals": {
        "total": 312,
        "distribution": {"respond": 89, "context": 198, "drop": 25},
        "avg_confidence": 0.723,
        "feature_averages": {"mention": 0.29, "reply_to_ene": 0.18},
        "override_count": 7
    },
    "daemon": {
        "total_events": 92,
        "by_type": {
            "classified": {"count": 89, "avg_duration_ms": 1200},
            "timeout": {"count": 3, "avg_duration_ms": 5000}
        }
    },
    "cleaning": {
        "total_events": 89,
        "by_type": {
            "cleaned": {"count": 89, "avg_duration_ms": 2}
        }
    },
    "memory": {
        "total_events": 14,
        "by_type": {
            "facts_extracted": {"count": 12},
            "reflection_generated": {"count": 2}
        }
    },
    "prompts": {"version": "1.0.0"}
}
```

### Other relevant endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/live` | GET (SSE) | Real-time event stream (includes `mod_*` events) |
| `/api/live/state` | GET | Current pipeline state snapshot |
| `/api/live/prompts` | GET (SSE) | Full prompt/response content stream |
| `/api/live/modules` | GET | Module health summary (JSON) |
| `/api/live/reset` | POST | Hard reset (clear queues, buffers, tracer) |

---

## Querying Data

All query methods are on `MetricsStore` (`nanobot/ene/observatory/store.py`).

### get_module_events(module, event_type=None, hours=24, limit=500)

Raw event rows for a module. Optionally filter by event_type.

```python
events = store.get_module_events("tracker", event_type="thread_created", hours=6)
for e in events:
    print(e["timestamp"], e["data"]["thread_id"], e["data"]["method"])
```

### get_module_summary(module, hours=24)

Aggregated event counts and average durations by event_type.

```python
summary = store.get_module_summary("daemon", hours=24)
# {"module": "daemon", "hours": 24, "total_events": 92,
#  "by_type": {"classified": {"count": 89, "avg_duration_ms": 1200, "max_duration_ms": 4800}, ...}}
```

### get_trace_events(trace_id)

All module events for a single message batch, ordered chronologically. Cross-module trace.

```python
events = store.get_trace_events("1708300000_discord:123_42")
for e in events:
    print(f"{e['module']}/{e['event_type']} {e.get('duration_ms', '')}ms")
```

### get_classification_stats(hours=24)

Classification distribution, average confidence, per-feature averages, and override count from signals events.

```python
stats = store.get_classification_stats(hours=24)
# {"total": 312, "distribution": {"respond": 89, "context": 198, "drop": 25},
#  "avg_confidence": 0.723, "feature_averages": {"mention": 0.29, ...}, "override_count": 7}
```

### get_thread_stats(hours=24)

Thread lifecycle stats from tracker events: creation count, assignment method distribution, average lifespan, messages per thread, active/background thread counts.

```python
stats = store.get_thread_stats(hours=24)
# {"threads_created": 47, "total_assignments": 312,
#  "assignment_methods": {"reply_to": 120, "scoring": 160, "pending_promote": 32},
#  "avg_lifespan_ms": 482300, "avg_messages_per_thread": 6.6, ...}
```

---

## File Reference

| File | Purpose |
|------|---------|
| `nanobot/ene/observatory/module_metrics.py` | `ModuleMetrics` + `NullModuleMetrics` classes |
| `nanobot/ene/observatory/store.py` | SQLite storage + all query methods |
| `nanobot/ene/observatory/tools.py` | `ViewModuleTool` (Dad-only) |
| `nanobot/ene/observatory/dashboard/api.py` | `/api/live/modules` endpoint |
| `nanobot/agent/live_trace.py` | `LiveTracer` (SSE ring buffer) |
| `nanobot/agent/prompts/loader.py` | `PromptLoader` |
| `nanobot/agent/prompts/manifest.json` | Prompt registry + version |
| `nanobot/agent/loop.py` | Wiring + trace_id generation |
