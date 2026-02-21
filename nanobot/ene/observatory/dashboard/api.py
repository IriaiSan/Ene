"""JSON API endpoints for the observatory dashboard.

All endpoints return JSON. Used by the dashboard frontend and
can also be called directly for programmatic access.
"""

from __future__ import annotations

import json
import time
from typing import Any, TYPE_CHECKING

from aiohttp import web
from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.live_trace import LiveTracer
    from nanobot.agent.loop import AgentLoop
    from nanobot.ene.observatory.store import MetricsStore
    from nanobot.ene.observatory.health import HealthMonitor
    from nanobot.ene.observatory.reporter import ReportGenerator


class ControlAPI:
    """Facade for control panel API endpoints.

    Late-bound — the loop reference is set after AgentLoop initializes modules.
    All properties return None when loop is not yet wired.
    """

    def __init__(self) -> None:
        self.loop: "AgentLoop | None" = None

    @property
    def social(self):
        """SocialModule — person profiles, trust."""
        if not self.loop:
            return None
        return self.loop.module_registry.get_module("social")

    @property
    def memory_mod(self):
        """MemoryModule — core memory, vector store."""
        if not self.loop:
            return None
        return self.loop.module_registry.get_module("memory")

    @property
    def conv_tracker(self):
        """ConversationTrackerModule — thread state."""
        if not self.loop:
            return None
        return self.loop.module_registry.get_module("conversation_tracker")

    @property
    def sessions(self):
        """SessionManager — session storage."""
        if not self.loop:
            return None
        return self.loop.sessions

    @property
    def daemon(self):
        """DaemonModule — subconscious pre-processor."""
        if not self.loop:
            return None
        return self.loop.module_registry.get_module("daemon")

    @property
    def config(self):
        """Full config object (sanitized before exposure)."""
        if not self.loop:
            return None
        return self.loop._config


def create_api_routes(
    store: "MetricsStore",
    health: "HealthMonitor | None" = None,
    reporter: "ReportGenerator | None" = None,
    live_tracer: "LiveTracer | None" = None,
    control: "ControlAPI | None" = None,
) -> tuple[list[web.RouteDef], Any, Any, "ControlAPI"]:
    """Create all API route definitions.

    Returns:
        Tuple of (routes, set_live_tracer_fn, set_reset_callback_fn, control_api).
        The setters allow wiring LiveTracer and the loop reset callback
        after the server has started (modules init later).
    """

    # Mutable container so set_live_tracer() can update after routes are created.
    _live_ref: list["LiveTracer | None"] = [live_tracer]
    _reset_cb: list[Any] = [None]
    _ctrl = control or ControlAPI()

    def _set_live_tracer(tracer: "LiveTracer") -> None:
        _live_ref[0] = tracer

    def _set_reset_callback(cb: Any) -> None:
        _reset_cb[0] = cb

    # ── Metrics endpoints (existing) ──────────────────────────────────────

    async def summary_today(request: web.Request) -> web.Response:
        data = store.get_today_summary()
        return web.json_response(data)

    async def summary_day(request: web.Request) -> web.Response:
        date = request.match_info.get("date", "")
        data = store.get_day_summary(date)
        return web.json_response(data)

    async def cost_daily(request: web.Request) -> web.Response:
        days = int(request.query.get("days", "30"))
        data = store.get_cost_by_day(min(days, 365))
        return web.json_response(data)

    async def cost_by_model(request: web.Request) -> web.Response:
        days = int(request.query.get("days", "7"))
        data = store.get_cost_by_model(min(days, 365))
        return web.json_response(data)

    async def cost_by_caller(request: web.Request) -> web.Response:
        days = int(request.query.get("days", "7"))
        data = store.get_cost_by_caller(min(days, 365))
        return web.json_response(data)

    async def cost_by_type(request: web.Request) -> web.Response:
        days = int(request.query.get("days", "7"))
        data = store.get_cost_by_type(min(days, 365))
        return web.json_response(data)

    async def activity_hourly(request: web.Request) -> web.Response:
        days = int(request.query.get("days", "1"))
        data = store.get_hourly_activity(min(days, 7))
        return web.json_response(data)

    async def latency(request: web.Request) -> web.Response:
        hours = int(request.query.get("hours", "24"))
        data = store.get_latency_percentiles(min(hours, 168))
        return web.json_response(data)

    async def errors(request: web.Request) -> web.Response:
        hours = int(request.query.get("hours", "24"))
        data = store.get_error_rate(min(hours, 168))
        return web.json_response(data)

    async def recent_calls(request: web.Request) -> web.Response:
        limit = int(request.query.get("limit", "50"))
        data = store.get_recent_calls(min(limit, 200))
        return web.json_response(data)

    async def health_status(request: web.Request) -> web.Response:
        if health:
            from dataclasses import asdict
            status = health.get_health_status()
            return web.json_response({
                "status": status.status,
                "checks": status.checks,
                "timestamp": status.timestamp,
            })
        return web.json_response({"status": "unavailable"})

    async def experiments_list(request: web.Request) -> web.Response:
        data = store.get_all_experiments()
        return web.json_response(data)

    async def experiment_detail(request: web.Request) -> web.Response:
        exp_id = request.match_info.get("id", "")
        experiment = store.get_experiment(exp_id)
        if not experiment:
            return web.json_response({"error": "not found"}, status=404)
        results = store.get_experiment_results(exp_id)
        experiment["results"] = results
        return web.json_response(experiment)

    async def sse_events(request: web.Request) -> web.StreamResponse:
        """Server-Sent Events stream for real-time updates."""
        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        response.headers["Access-Control-Allow-Origin"] = "*"
        await response.prepare(request)

        import asyncio
        try:
            while True:
                summary = store.get_today_summary()
                event_data = json.dumps(summary)
                await response.write(f"event: summary\ndata: {event_data}\n\n".encode())
                await asyncio.sleep(5)
        except (asyncio.CancelledError, ConnectionResetError):
            pass

        return response

    # ── Live Trace endpoints ──────────────────────────────────────────────

    async def live_stream(request: web.Request) -> web.StreamResponse:
        """SSE stream for real-time processing events from the agent loop."""
        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        response.headers["Access-Control-Allow-Origin"] = "*"
        await response.prepare(request)

        tracer = _live_ref[0]
        if not tracer:
            await response.write(b"event: error\ndata: {\"error\": \"Live tracer not available\"}\n\n")
            return response

        import asyncio as _aio

        last_id = int(request.query.get("last_id", "0"))

        try:
            catchup = tracer.get_events_since(last_id)
            for evt in catchup:
                evt_data = json.dumps(evt)
                await response.write(f"event: event\ndata: {evt_data}\n\n".encode())
                last_id = evt["id"]

            state = tracer.get_state()
            state_data = json.dumps(state)
            await response.write(f"event: state\ndata: {state_data}\n\n".encode())

            while True:
                got_event = await tracer.wait_for_event(timeout=15.0)
                if got_event:
                    new_events = tracer.get_events_since(last_id)
                    for evt in new_events:
                        evt_data = json.dumps(evt)
                        await response.write(f"event: event\ndata: {evt_data}\n\n".encode())
                        last_id = evt["id"]
                else:
                    await response.write(b": heartbeat\n\n")

                state = tracer.get_state()
                state_data = json.dumps(state)
                await response.write(f"event: state\ndata: {state_data}\n\n".encode())

        except (_aio.CancelledError, ConnectionResetError, ConnectionAbortedError):
            pass

        return response

    async def live_state(request: web.Request) -> web.Response:
        """REST endpoint for live pipeline state snapshot."""
        tracer = _live_ref[0]
        if not tracer:
            return web.json_response({"error": "Live tracer not available"}, status=503)
        state = tracer.get_state()
        # Inject brain + model status into state
        if _ctrl.loop:
            state["brain_enabled"] = _ctrl.loop.is_brain_enabled()
            state["current_model"] = _ctrl.loop.get_model()
        return web.json_response(state)

    async def prompt_stream(request: web.Request) -> web.StreamResponse:
        """SSE stream for full prompt/response content (daemon + Ene)."""
        response = web.StreamResponse()
        response.headers["Content-Type"] = "text/event-stream"
        response.headers["Cache-Control"] = "no-cache"
        response.headers["Connection"] = "keep-alive"
        response.headers["Access-Control-Allow-Origin"] = "*"
        await response.prepare(request)

        tracer = _live_ref[0]
        if not tracer:
            await response.write(b"event: error\ndata: {\"error\": \"Live tracer not available\"}\n\n")
            return response

        import asyncio as _aio

        last_id = int(request.query.get("last_id", "0"))

        try:
            catchup = tracer.get_prompts_since(last_id)
            for entry in catchup:
                data = json.dumps(entry)
                await response.write(f"event: prompt\ndata: {data}\n\n".encode())
                last_id = entry["id"]

            while True:
                got = await tracer.wait_for_prompt(timeout=15.0)
                if got:
                    new_entries = tracer.get_prompts_since(last_id)
                    for entry in new_entries:
                        data = json.dumps(entry)
                        await response.write(f"event: prompt\ndata: {data}\n\n".encode())
                        last_id = entry["id"]
                else:
                    await response.write(b": heartbeat\n\n")

        except (_aio.CancelledError, ConnectionResetError, ConnectionAbortedError):
            pass

        return response

    async def module_health(request: web.Request) -> web.Response:
        """Module health summary."""
        hours = int(request.query.get("hours", "1"))
        hours = min(hours, 168)

        modules = {}

        try:
            thread_stats = store.get_thread_stats(hours=hours)
            modules["tracker"] = thread_stats
        except Exception:
            modules["tracker"] = {"error": "unavailable"}

        try:
            cls_stats = store.get_classification_stats(hours=hours)
            modules["signals"] = cls_stats
        except Exception:
            modules["signals"] = {"error": "unavailable"}

        try:
            daemon_events = store.get_module_summary("daemon", hours=hours)
            modules["daemon"] = daemon_events
        except Exception:
            modules["daemon"] = {"error": "unavailable"}

        try:
            cleaning_events = store.get_module_summary("cleaning", hours=hours)
            modules["cleaning"] = cleaning_events
        except Exception:
            modules["cleaning"] = {"error": "unavailable"}

        try:
            memory_events = store.get_module_summary("memory", hours=hours)
            modules["memory"] = memory_events
        except Exception:
            modules["memory"] = {"error": "unavailable"}

        try:
            from nanobot.agent.prompts.loader import PromptLoader
            modules["prompts"] = {"version": PromptLoader().version}
        except Exception:
            modules["prompts"] = {"version": "unknown"}

        return web.json_response(modules)

    async def live_hard_reset(request: web.Request) -> web.Response:
        """Hard reset: drop all queues/buffers, clear session context, wipe tracer."""
        tracer = _live_ref[0]
        reset_cb = _reset_cb[0]

        if not tracer:
            return web.json_response({"error": "Live tracer not available"}, status=503)

        if reset_cb is not None:
            try:
                import asyncio as _aio
                result = reset_cb()
                if _aio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning(f"Hard reset callback error: {e}")

        tracer.hard_reset()

        logger.info("Dashboard: hard reset triggered")
        return web.json_response({"ok": True, "message": "Hard reset complete"})

    # ── Brain toggle endpoints ────────────────────────────────────────────

    async def brain_status(request: web.Request) -> web.Response:
        """Get brain (LLM response) toggle status."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)
        return web.json_response({"enabled": _ctrl.loop.is_brain_enabled()})

    async def brain_pause(request: web.Request) -> web.Response:
        """Pause the brain — stop LLM calls, keep everything else running."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)
        _ctrl.loop.pause_brain()
        return web.json_response({"ok": True, "enabled": False})

    async def brain_resume(request: web.Request) -> web.Response:
        """Resume the brain — start processing messages via LLM again."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)
        _ctrl.loop.resume_brain()
        return web.json_response({"ok": True, "enabled": True})

    # ── Model switching endpoints ────────────────────────────────────────

    async def model_get(request: web.Request) -> web.Response:
        """Get the current primary model."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)
        return web.json_response({"model": _ctrl.loop.get_model()})

    async def model_set(request: web.Request) -> web.Response:
        """Switch the primary model at runtime."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        model = body.get("model", "").strip()
        if not model:
            return web.json_response({"error": "model required"}, status=400)

        old = _ctrl.loop.set_model(model)
        return web.json_response({"ok": True, "old": old, "new": model})

    async def model_options(request: web.Request) -> web.Response:
        """List known model options for the dropdown."""
        from nanobot.providers.litellm_provider import DEFAULT_FALLBACK_MODELS

        # Start with the fallback list, add extras for A/B testing
        known = list(DEFAULT_FALLBACK_MODELS)
        extras = [
            "deepseek/deepseek-r1",
            "deepseek/deepseek-v3",
            "qwen/qwen3-30b-a3b",
            "google/gemini-2.5-pro",
            "anthropic/claude-sonnet-4",
        ]
        for m in extras:
            if m not in known:
                known.append(m)

        # Include current model if not already in list
        if _ctrl.loop:
            current = _ctrl.loop.get_model()
            if current not in known:
                known.insert(0, current)

        return web.json_response({"models": known})

    # ── People endpoints (Social Registry) ────────────────────────────────

    async def people_list(request: web.Request) -> web.Response:
        """List all person profiles (compact)."""
        social = _ctrl.social
        if not social or not hasattr(social, '_registry') or not social._registry:
            return web.json_response([])

        registry = social._registry
        people = registry.get_all()
        result = []
        for p in people:
            signals = p.trust.signals if hasattr(p.trust, 'signals') else {}
            result.append({
                "id": p.id,
                "name": p.display_name,
                "aliases": p.aliases[:3] if hasattr(p, 'aliases') else [],
                "tier": p.trust.tier,
                "score": round(p.trust.score * 100, 1),
                "msg_count": signals.get("message_count", 0),
                "days_active": signals.get("days_active", 0),
                "platform_ids": list(p.platform_ids.keys()) if hasattr(p, 'platform_ids') else [],
            })
        # Sort by trust score descending
        result.sort(key=lambda x: x["score"], reverse=True)
        return web.json_response(result)

    async def person_detail(request: web.Request) -> web.Response:
        """Get full detail for a person profile."""
        social = _ctrl.social
        if not social or not hasattr(social, '_registry') or not social._registry:
            return web.json_response({"error": "Social module not available"}, status=503)

        pid = request.match_info.get("id", "")
        registry = social._registry
        person = registry.get_by_id(pid)
        if not person:
            # Try by platform ID
            person = registry.get_by_platform_id(pid)
        if not person:
            return web.json_response({"error": "Person not found"}, status=404)

        trust = person.trust
        signals = trust.signals if hasattr(trust, 'signals') else {}
        violations = []
        if hasattr(trust, 'violations'):
            for v in trust.violations:
                violations.append({
                    "description": v.description if hasattr(v, 'description') else str(v),
                    "severity": v.severity if hasattr(v, 'severity') else 0,
                    "timestamp": v.timestamp if hasattr(v, 'timestamp') else None,
                })

        notes = []
        if hasattr(person, 'notes'):
            for n in person.notes:
                if isinstance(n, dict):
                    notes.append(n)
                elif hasattr(n, 'content'):
                    notes.append({"content": n.content, "added_at": getattr(n, 'added_at', '')})
                else:
                    notes.append({"content": str(n)})

        history = []
        if hasattr(trust, 'history'):
            history = trust.history[-30:]  # Last 30 entries

        result = {
            "id": person.id,
            "name": person.display_name,
            "aliases": person.aliases if hasattr(person, 'aliases') else [],
            "platform_ids": {
                k: v.to_dict() if hasattr(v, 'to_dict') else str(v)
                for k, v in person.platform_ids.items()
            } if hasattr(person, 'platform_ids') else {},
            "summary": person.summary if hasattr(person, 'summary') else "",
            "trust": {
                "score": round(trust.score * 100, 1),
                "tier": trust.tier,
                "positive": trust.positive_interactions if hasattr(trust, 'positive_interactions') else 0,
                "negative": trust.negative_interactions if hasattr(trust, 'negative_interactions') else 0,
                "manual_override": trust.manual_override if hasattr(trust, 'manual_override') else None,
                "signals": signals,
                "violations": violations,
                "history": history,
            },
            "notes": notes,
            "connections": [
                c.to_dict() if hasattr(c, 'to_dict') else c
                for c in person.connections
            ] if hasattr(person, 'connections') else [],
        }
        return web.json_response(result)

    async def person_update(request: web.Request) -> web.Response:
        """Update person profile (summary, manual_override)."""
        social = _ctrl.social
        if not social or not hasattr(social, '_registry') or not social._registry:
            return web.json_response({"error": "Social module not available"}, status=503)

        pid = request.match_info.get("id", "")
        registry = social._registry
        person = registry.get_by_id(pid)
        if not person:
            person = registry.get_by_platform_id(pid)
        if not person:
            return web.json_response({"error": "Person not found"}, status=404)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        if "summary" in body:
            person.summary = str(body["summary"])[:500]

        if "manual_override" in body:
            val = body["manual_override"]
            if val is None:
                person.trust.manual_override = None
            else:
                try:
                    val = float(val)
                    person.trust.manual_override = max(0.0, min(1.0, val))
                except (TypeError, ValueError):
                    return web.json_response({"error": "manual_override must be a number 0-1 or null"}, status=400)

        registry.update(person)
        return web.json_response({"ok": True})

    async def person_add_note(request: web.Request) -> web.Response:
        """Add a note to a person profile."""
        social = _ctrl.social
        if not social or not hasattr(social, '_registry') or not social._registry:
            return web.json_response({"error": "Social module not available"}, status=503)

        pid = request.match_info.get("id", "")
        registry = social._registry

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        content = body.get("content", "").strip()
        if not content:
            return web.json_response({"error": "Note content required"}, status=400)

        ok = registry.add_note(pid, content)
        if not ok:
            return web.json_response({"error": "Person not found or note limit reached"}, status=404)
        return web.json_response({"ok": True})

    # ── Memory endpoints (Core Memory) ────────────────────────────────────

    async def memory_get(request: web.Request) -> web.Response:
        """Get all core memory sections with entries and token stats."""
        mem = _ctrl.memory_mod
        if not mem or not hasattr(mem, '_system') or not mem._system:
            return web.json_response({"error": "Memory module not available"}, status=503)

        core = mem._system.core
        raw_sections = core._data.get("sections", {})
        sections = {}
        for section_name, sec_data in raw_sections.items():
            entries = sec_data.get("entries", [])
            section_tokens = core.get_section_tokens(section_name)
            sections[section_name] = {
                "label": sec_data.get("label", section_name),
                "max_tokens": sec_data.get("max_tokens", 0),
                "used_tokens": section_tokens,
                "entries": [
                    {
                        "id": e.get("id", ""),
                        "content": e.get("content", ""),
                        "importance": e.get("importance", 5),
                        "created_at": e.get("created_at", ""),
                        "updated_at": e.get("updated_at", ""),
                    }
                    for e in entries
                ],
            }

        return web.json_response({
            "sections": sections,
            "total_tokens": core.get_total_tokens(),
            "budget": core.token_budget,
            "budget_remaining": core.budget_remaining,
        })

    async def memory_add_entry(request: web.Request) -> web.Response:
        """Add a new entry to a core memory section."""
        mem = _ctrl.memory_mod
        if not mem or not hasattr(mem, '_system') or not mem._system:
            return web.json_response({"error": "Memory module not available"}, status=503)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        section = body.get("section", "")
        content = body.get("content", "").strip()
        importance = int(body.get("importance", 5))

        if not section or not content:
            return web.json_response({"error": "section and content required"}, status=400)

        core = mem._system.core
        entry_id = core.add_entry(section, content, importance=importance)
        if entry_id is None:
            return web.json_response({"error": "Failed to add entry (section not found or over budget)"}, status=400)

        core.save()
        return web.json_response({"ok": True, "id": entry_id})

    async def memory_edit_entry(request: web.Request) -> web.Response:
        """Edit a core memory entry."""
        mem = _ctrl.memory_mod
        if not mem or not hasattr(mem, '_system') or not mem._system:
            return web.json_response({"error": "Memory module not available"}, status=503)

        entry_id = request.match_info.get("id", "")
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        core = mem._system.core
        ok = core.edit_entry(
            entry_id,
            new_content=body.get("content"),
            new_section=body.get("section"),
            importance=body.get("importance"),
        )
        if not ok:
            return web.json_response({"error": "Entry not found or edit failed"}, status=404)

        core.save()
        return web.json_response({"ok": True})

    async def memory_delete_entry(request: web.Request) -> web.Response:
        """Delete a core memory entry."""
        mem = _ctrl.memory_mod
        if not mem or not hasattr(mem, '_system') or not mem._system:
            return web.json_response({"error": "Memory module not available"}, status=503)

        entry_id = request.match_info.get("id", "")
        core = mem._system.core
        deleted = core.delete_entry(entry_id)
        if deleted is None:
            return web.json_response({"error": "Entry not found"}, status=404)

        core.save()
        return web.json_response({"ok": True, "deleted": deleted})

    # ── Thread endpoints (Conversation Tracker) ───────────────────────────

    async def threads_list(request: web.Request) -> web.Response:
        """List all active threads. Pass ?full=1 to include messages."""
        tracker_mod = _ctrl.conv_tracker
        if not tracker_mod or not hasattr(tracker_mod, 'tracker') or not tracker_mod.tracker:
            return web.json_response([])

        tracker = tracker_mod.tracker
        channel = request.query.get("channel", None)
        include_messages = request.query.get("full", "") == "1"
        threads = []
        for tid, thread in tracker._threads.items():
            if channel and thread.channel_key != channel:
                continue
            # Get unique participants
            participants = set()
            for m in thread.messages:
                if not m.is_ene:
                    participants.add(m.author_name)
            entry = {
                "id": tid,
                "channel": thread.channel_key,
                "state": thread.state,
                "msg_count": len(thread.messages),
                "participants": list(participants)[:6],
                "ene_involved": thread.ene_involved,
                "ene_responded": getattr(thread, 'ene_responded', False),
                "created_at": thread.created_at,
                "last_activity": thread.messages[-1].timestamp if thread.messages else thread.created_at,
            }
            if include_messages:
                entry["messages"] = [
                    {
                        "author": m.author_name,
                        "content": m.content[:500],
                        "timestamp": m.timestamp,
                        "is_ene": m.is_ene,
                        "classification": m.classification if hasattr(m, 'classification') else None,
                    }
                    for m in thread.messages
                ]
            threads.append(entry)
        threads.sort(key=lambda x: x["last_activity"], reverse=True)
        return web.json_response(threads)

    async def thread_detail(request: web.Request) -> web.Response:
        """Get thread detail with messages."""
        tracker_mod = _ctrl.conv_tracker
        if not tracker_mod or not hasattr(tracker_mod, 'tracker') or not tracker_mod.tracker:
            return web.json_response({"error": "Tracker not available"}, status=503)

        tid = request.match_info.get("id", "")
        tracker = tracker_mod.tracker
        thread = tracker._threads.get(tid)
        if not thread:
            return web.json_response({"error": "Thread not found"}, status=404)

        messages = []
        for m in thread.messages:
            messages.append({
                "msg_id": m.discord_msg_id,
                "author": m.author_name,
                "content": m.content[:500],
                "timestamp": m.timestamp,
                "is_ene": m.is_ene,
                "classification": m.classification if hasattr(m, 'classification') else None,
            })

        return web.json_response({
            "id": tid,
            "channel": thread.channel_key,
            "state": thread.state,
            "ene_involved": thread.ene_involved,
            "ene_responded": getattr(thread, 'ene_responded', False),
            "last_shown_index": thread.last_shown_index,
            "created_at": thread.created_at,
            "messages": messages,
        })

    async def thread_update(request: web.Request) -> web.Response:
        """Update thread state (e.g., archive by setting to DEAD)."""
        tracker_mod = _ctrl.conv_tracker
        if not tracker_mod or not hasattr(tracker_mod, 'tracker') or not tracker_mod.tracker:
            return web.json_response({"error": "Tracker not available"}, status=503)

        tid = request.match_info.get("id", "")
        tracker = tracker_mod.tracker
        thread = tracker._threads.get(tid)
        if not thread:
            return web.json_response({"error": "Thread not found"}, status=404)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        new_state = body.get("state")
        if new_state:
            from nanobot.ene.conversation.models import DEAD
            thread.state = new_state
        return web.json_response({"ok": True})

    async def threads_pending(request: web.Request) -> web.Response:
        """Get pending (unassigned) messages."""
        tracker_mod = _ctrl.conv_tracker
        if not tracker_mod or not hasattr(tracker_mod, 'tracker') or not tracker_mod.tracker:
            return web.json_response([])

        tracker = tracker_mod.tracker
        pending = []
        for pm in tracker._pending:
            msg = pm.message
            pending.append({
                "channel": pm.channel_key,
                "author": msg.author_name,
                "content": msg.content[:200],
                "timestamp": msg.timestamp,
            })
        return web.json_response(pending)

    # ── Session endpoints ─────────────────────────────────────────────────

    async def sessions_list(request: web.Request) -> web.Response:
        """List all sessions with metadata."""
        sessions = _ctrl.sessions
        if not sessions:
            return web.json_response([])

        try:
            session_list = sessions.list_sessions()
        except Exception:
            session_list = []

        return web.json_response(session_list)

    async def session_history(request: web.Request) -> web.Response:
        """Get recent messages from a session."""
        sessions = _ctrl.sessions
        if not sessions:
            return web.json_response({"error": "Sessions not available"}, status=503)

        key = request.match_info.get("key", "")
        limit = int(request.query.get("limit", "20"))

        try:
            session = sessions.get_or_create(key)
            history = session.get_history(max_messages=min(limit, 100))
            token_est = session.estimate_tokens()
            responded = session.get_responded_count()
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

        return web.json_response({
            "key": key,
            "messages": history,
            "token_estimate": token_est,
            "responded_count": responded,
        })

    async def session_clear(request: web.Request) -> web.Response:
        """Clear a session (dangerous — deletes history)."""
        sessions = _ctrl.sessions
        if not sessions:
            return web.json_response({"error": "Sessions not available"}, status=503)

        key = request.match_info.get("key", "")
        try:
            session = sessions.get_or_create(key)
            session.clear()
            sessions.save(session)
            sessions.invalidate(key)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=500)

        return web.json_response({"ok": True, "message": f"Session {key} cleared"})

    # ── Security endpoints ────────────────────────────────────────────────

    async def security_state(request: web.Request) -> web.Response:
        """Get security state: muted users, rate limits, jailbreak scores."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)

        now = time.time()
        muted = _ctrl.loop.get_muted_users()
        muted_list = [
            {"caller_id": uid, "expires_at": exp, "remaining_sec": round(exp - now)}
            for uid, exp in muted.items()
        ]

        rate_limits = _ctrl.loop.get_rate_limit_state()
        rate_list = []
        for uid, timestamps in rate_limits.items():
            recent = [t for t in timestamps if t > now - _ctrl.loop._rate_limit_window]
            if recent:
                rate_list.append({
                    "caller_id": uid,
                    "count": len(recent),
                    "max": _ctrl.loop._rate_limit_max,
                    "window_sec": _ctrl.loop._rate_limit_window,
                })

        jailbreak = _ctrl.loop.get_jailbreak_scores()
        jb_list = []
        for uid, timestamps in jailbreak.items():
            recent = [t for t in timestamps if t > now - 300]  # 5 min window
            if recent:
                jb_list.append({
                    "caller_id": uid,
                    "count": len(recent),
                    "threshold": _ctrl.loop._jailbreak_threshold,
                })

        return web.json_response({
            "muted": muted_list,
            "rate_limits": rate_list,
            "jailbreak_scores": jb_list,
        })

    async def security_mute(request: web.Request) -> web.Response:
        """Mute a user."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        caller_id = body.get("caller_id", "").strip()
        duration_min = float(body.get("duration_min", 30))

        if not caller_id:
            return web.json_response({"error": "caller_id required"}, status=400)

        _ctrl.loop.mute_user(caller_id, duration_min)
        return web.json_response({"ok": True})

    async def security_unmute(request: web.Request) -> web.Response:
        """Unmute a user."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)

        caller_id = request.match_info.get("caller_id", "")
        ok = _ctrl.loop.unmute_user(caller_id)
        return web.json_response({"ok": ok})

    async def security_clear_rate_limit(request: web.Request) -> web.Response:
        """Clear rate limit for a user."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)

        caller_id = request.match_info.get("caller_id", "")
        ok = _ctrl.loop.clear_rate_limit(caller_id)
        return web.json_response({"ok": ok})

    # ── Config endpoint ───────────────────────────────────────────────────

    async def config_get(request: web.Request) -> web.Response:
        """Get current running config (sanitized — no API keys)."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)

        result = {
            "agent": {
                "model": _ctrl.loop.model,
                "consolidation_model": _ctrl.loop.consolidation_model,
                "temperature": _ctrl.loop.temperature,
                "max_tokens": _ctrl.loop.max_tokens,
                "max_iterations": _ctrl.loop.max_iterations,
                "memory_window": _ctrl.loop.memory_window,
            },
            "debounce": {
                "window_sec": _ctrl.loop._debounce_window,
                "batch_limit": _ctrl.loop._debounce_batch_limit,
                "max_buffer": _ctrl.loop._debounce_max_buffer,
                "queue_merge_cap": _ctrl.loop._queue_merge_cap,
            },
            "rate_limit": {
                "window_sec": _ctrl.loop._rate_limit_window,
                "max_messages": _ctrl.loop._rate_limit_max,
            },
            "modules": list(_ctrl.loop.module_registry.modules.keys()),
        }

        # Add config-file settings if available (sanitized)
        cfg = _ctrl.config
        if cfg:
            try:
                if hasattr(cfg, 'agents') and hasattr(cfg.agents, 'defaults'):
                    defaults = cfg.agents.defaults
                    if hasattr(defaults, 'observatory'):
                        obs = defaults.observatory
                        result["observatory"] = {
                            "enabled": obs.enabled if hasattr(obs, 'enabled') else True,
                            "dashboard_port": obs.dashboard_port if hasattr(obs, 'dashboard_port') else 18791,
                            "cost_spike_multiplier": obs.cost_spike_multiplier if hasattr(obs, 'cost_spike_multiplier') else 2.0,
                            "error_rate_threshold": obs.error_rate_threshold if hasattr(obs, 'error_rate_threshold') else 0.1,
                            "latency_p95_threshold": obs.latency_p95_threshold if hasattr(obs, 'latency_p95_threshold') else 30.0,
                        }
            except Exception:
                pass

        return web.json_response(result)

    # ── Context snapshot (combined memory + threads + session) ───────────

    async def context_snapshot(request: web.Request) -> web.Response:
        """Combined snapshot of what Ene currently 'knows' — memory, threads, session.

        Used by the live dashboard context panel to show real-time state.
        Polls every few seconds so the user can watch state evolve.
        """
        result: dict[str, Any] = {}

        # Core memory
        mem = _ctrl.memory_mod
        if mem and hasattr(mem, '_system') and mem._system:
            core = mem._system.core
            raw = core._data.get("sections", {})
            sections = {}
            for name, sec in raw.items():
                entries = sec.get("entries", [])
                sections[name] = {
                    "label": sec.get("label", name),
                    "used_tokens": core.get_section_tokens(name),
                    "max_tokens": sec.get("max_tokens", 0),
                    "count": len(entries),
                    "entries": [
                        {
                            "id": e.get("id", "")[:8],
                            "content": e.get("content", "")[:200],
                            "importance": e.get("importance", 5),
                            "updated_at": e.get("updated_at", e.get("created_at", "")),
                        }
                        for e in entries
                    ],
                }
            result["memory"] = {
                "sections": sections,
                "total_tokens": core.get_total_tokens(),
                "budget": core.token_budget,
            }

        # Active threads
        tracker_mod = _ctrl.conv_tracker
        if tracker_mod and hasattr(tracker_mod, 'tracker') and tracker_mod.tracker:
            tracker = tracker_mod.tracker
            threads = []
            for tid, thread in tracker._threads.items():
                participants = set()
                for m in thread.messages:
                    if not m.is_ene:
                        participants.add(m.author_name)
                last_msgs = thread.messages[-5:] if thread.messages else []
                threads.append({
                    "id": tid[:8],
                    "channel": thread.channel_key,
                    "state": thread.state,
                    "msg_count": len(thread.messages),
                    "participants": list(participants)[:4],
                    "ene_involved": thread.ene_involved,
                    "last_activity": thread.messages[-1].timestamp if thread.messages else 0,
                    "last_shown_index": getattr(thread, 'last_shown_index', 0),
                    "recent_messages": [
                        {
                            "author": "Ene" if m.is_ene else m.author_name,
                            "content": m.content[:150],
                            "ts": m.timestamp,
                        }
                        for m in last_msgs
                    ],
                })
            threads.sort(key=lambda x: x["last_activity"], reverse=True)

            pending = []
            for pm in tracker._pending:
                pending.append({
                    "author": pm.message.author_name or pm.message.author_id,
                    "content": pm.message.content[:150],
                    "channel": pm.channel_key,
                })

            result["threads"] = {"active": threads, "pending": pending}

        # Session state (most recent entries)
        sessions = _ctrl.sessions
        if sessions:
            try:
                session_list = sessions.list_sessions()
                active_sessions = []
                for s in session_list:
                    key = s.get("key", "")
                    if not key:
                        continue
                    try:
                        session = sessions.get_or_create(key)
                        history = session.get_history(max_messages=6)
                        active_sessions.append({
                            "key": key,
                            "msg_count": s.get("msg_count", len(history)),
                            "token_estimate": session.estimate_tokens(),
                            "responded_count": session.get_responded_count(),
                            "recent": [
                                {
                                    "role": m.get("role", "?"),
                                    "content": m.get("content", "")[:150],
                                }
                                for m in history[-6:]
                            ],
                        })
                    except Exception:
                        continue
                result["sessions"] = active_sessions
            except Exception:
                result["sessions"] = []

        return web.json_response(result)

    # ── Settings endpoints (model config + custom LLMs) ──────────────────

    # Custom models persist to workspace/settings/custom_models.json
    _custom_models_path: Any = None  # Set lazily on first access

    def _get_custom_models_path():
        """Resolve the custom models storage path (lazy)."""
        nonlocal _custom_models_path
        if _custom_models_path is not None:
            return _custom_models_path
        from pathlib import Path
        workspace = Path.home() / ".nanobot" / "workspace" / "settings"
        workspace.mkdir(parents=True, exist_ok=True)
        _custom_models_path = workspace / "custom_models.json"
        return _custom_models_path

    def _load_custom_models() -> list[dict]:
        """Load custom models from disk."""
        path = _get_custom_models_path()
        if not path.exists():
            return []
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

    def _save_custom_models(models: list[dict]) -> None:
        """Persist custom models to disk and update pricing table."""
        path = _get_custom_models_path()
        path.write_text(json.dumps(models, indent=2), encoding="utf-8")
        # Update runtime pricing table
        from nanobot.ene.observatory.pricing import MODEL_PRICING
        for m in models:
            MODEL_PRICING[m["id"]] = {"input": m.get("input_price", 1.0), "output": m.get("output_price", 2.0)}

    async def settings_get(request: web.Request) -> web.Response:
        """Get full settings state: all model slots, custom models, known models."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)

        from nanobot.ene.observatory.pricing import MODEL_PRICING
        from nanobot.providers.litellm_provider import DEFAULT_FALLBACK_MODELS

        # Current model slots
        primary = _ctrl.loop.get_model()
        consolidation = _ctrl.loop.consolidation_model or ""

        # Daemon model
        daemon_model = ""
        daemon_mod = _ctrl.daemon
        if daemon_mod and daemon_mod.processor:
            daemon_model = daemon_mod.processor._model or ""

        # Fallback models
        fallbacks = list(DEFAULT_FALLBACK_MODELS)

        # Build known models list (all unique models from pricing + fallbacks + extras)
        known_extras = [
            "deepseek/deepseek-r1",
            "deepseek/deepseek-v3",
            "qwen/qwen3-30b-a3b",
            "qwen/qwen3-235b-a22b",
            "google/gemini-2.5-pro",
            "google/gemini-2.5-flash",
            "google/gemini-3-flash-preview",
            "anthropic/claude-sonnet-4",
        ]
        known_models = sorted(set(
            list(MODEL_PRICING.keys()) + fallbacks + known_extras
        ))
        # Include current models if not in list
        for m in [primary, consolidation, daemon_model]:
            if m and m not in known_models:
                known_models.insert(0, m)

        # Pricing info
        pricing = {}
        for model_id, prices in MODEL_PRICING.items():
            pricing[model_id] = {"input": prices["input"], "output": prices["output"]}

        # Custom models
        custom_models = _load_custom_models()

        return web.json_response({
            "models": {
                "primary": primary,
                "consolidation": consolidation,
                "daemon": daemon_model,
            },
            "fallback_models": fallbacks,
            "known_models": known_models,
            "pricing": pricing,
            "custom_models": custom_models,
        })

    async def settings_set_model(request: web.Request) -> web.Response:
        """Set a model slot (primary, consolidation, daemon)."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)

        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        slot = body.get("slot", "").strip()
        model = body.get("model", "").strip()

        if slot not in ("primary", "consolidation", "daemon"):
            return web.json_response({"error": "slot must be primary, consolidation, or daemon"}, status=400)
        if not model:
            return web.json_response({"error": "model required"}, status=400)

        result = {"slot": slot, "model": model}

        if slot == "primary":
            old = _ctrl.loop.set_model(model)
            result["old"] = old

        elif slot == "consolidation":
            old = _ctrl.loop.consolidation_model
            _ctrl.loop.consolidation_model = model
            result["old"] = old or ""
            logger.info(f"Consolidation model set: {old} → {model}")

        elif slot == "daemon":
            daemon_mod = _ctrl.daemon
            if daemon_mod and daemon_mod.processor:
                old = daemon_mod.processor._model or ""
                daemon_mod.processor._model = model
                # Reset rotation state
                daemon_mod.processor._model_index = 0
                daemon_mod.processor._model_failures.clear()
                result["old"] = old
                logger.info(f"Daemon model set: {old} → {model}")
            else:
                return web.json_response({"error": "Daemon module not available"}, status=503)

        return web.json_response({"ok": True, **result})

    async def settings_clear_daemon_model(request: web.Request) -> web.Response:
        """Clear daemon model override → revert to free model rotation."""
        if not _ctrl.loop:
            return web.json_response({"error": "Agent loop not available"}, status=503)

        daemon_mod = _ctrl.daemon
        if daemon_mod and daemon_mod.processor:
            old = daemon_mod.processor._model or ""
            daemon_mod.processor._model = None
            daemon_mod.processor._model_index = 0
            daemon_mod.processor._model_failures.clear()
            logger.info(f"Daemon model cleared (was: {old}), reverted to free rotation")
            return web.json_response({"ok": True, "old": old, "mode": "free_rotation"})
        return web.json_response({"error": "Daemon module not available"}, status=503)

    async def custom_models_list(request: web.Request) -> web.Response:
        """List all custom models."""
        return web.json_response(_load_custom_models())

    async def custom_models_add(request: web.Request) -> web.Response:
        """Add a custom model."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": "Invalid JSON"}, status=400)

        model_id = body.get("id", "").strip()
        if not model_id:
            return web.json_response({"error": "Model ID required"}, status=400)
        if "/" not in model_id:
            return web.json_response({"error": "Model ID should be provider/model format (e.g. openai/gpt-4o)"}, status=400)

        input_price = float(body.get("input_price", 1.0))
        output_price = float(body.get("output_price", 2.0))
        label = body.get("label", "").strip() or model_id

        models = _load_custom_models()

        # Check for duplicates
        existing = next((m for m in models if m["id"] == model_id), None)
        if existing:
            # Update existing
            existing["input_price"] = input_price
            existing["output_price"] = output_price
            existing["label"] = label
        else:
            models.append({
                "id": model_id,
                "label": label,
                "input_price": input_price,
                "output_price": output_price,
            })

        _save_custom_models(models)
        return web.json_response({"ok": True, "model": model_id, "count": len(models)})

    async def custom_models_delete(request: web.Request) -> web.Response:
        """Delete a custom model."""
        model_id = request.match_info.get("model_id", "")
        if not model_id:
            return web.json_response({"error": "model_id required"}, status=400)

        models = _load_custom_models()
        original_count = len(models)
        models = [m for m in models if m["id"] != model_id]

        if len(models) == original_count:
            return web.json_response({"error": "Model not found"}, status=404)

        _save_custom_models(models)

        # Remove from runtime pricing if it was added
        from nanobot.ene.observatory.pricing import MODEL_PRICING
        MODEL_PRICING.pop(model_id, None)

        return web.json_response({"ok": True, "deleted": model_id, "count": len(models)})

    # ── Route definitions ─────────────────────────────────────────────────

    routes = [
        # Metrics (existing)
        web.get("/api/summary/today", summary_today),
        web.get("/api/summary/{date}", summary_day),
        web.get("/api/cost/daily", cost_daily),
        web.get("/api/cost/by-model", cost_by_model),
        web.get("/api/cost/by-caller", cost_by_caller),
        web.get("/api/cost/by-type", cost_by_type),
        web.get("/api/activity/hourly", activity_hourly),
        web.get("/api/latency", latency),
        web.get("/api/errors", errors),
        web.get("/api/calls/recent", recent_calls),
        web.get("/api/health", health_status),
        web.get("/api/experiments", experiments_list),
        web.get("/api/experiments/{id}", experiment_detail),
        web.get("/api/events", sse_events),
        # Live trace (existing)
        web.get("/api/live", live_stream),
        web.get("/api/live/state", live_state),
        web.get("/api/live/prompts", prompt_stream),
        web.get("/api/live/modules", module_health),
        web.post("/api/live/reset", live_hard_reset),
        web.get("/api/live/context", context_snapshot),
        # Brain toggle (new)
        web.get("/api/brain", brain_status),
        web.post("/api/brain/pause", brain_pause),
        web.post("/api/brain/resume", brain_resume),
        # Model switching (new)
        web.get("/api/model", model_get),
        web.post("/api/model", model_set),
        web.get("/api/model/options", model_options),
        # People (new)
        web.get("/api/people", people_list),
        web.get("/api/people/{id}", person_detail),
        web.patch("/api/people/{id}", person_update),
        web.post("/api/people/{id}/notes", person_add_note),
        # Memory (new)
        web.get("/api/memory", memory_get),
        web.post("/api/memory/entries", memory_add_entry),
        web.patch("/api/memory/entries/{id}", memory_edit_entry),
        web.delete("/api/memory/entries/{id}", memory_delete_entry),
        # Threads (new)
        web.get("/api/threads", threads_list),
        web.get("/api/threads/pending", threads_pending),
        web.get("/api/threads/{id}", thread_detail),
        web.patch("/api/threads/{id}", thread_update),
        # Sessions (new)
        web.get("/api/sessions", sessions_list),
        web.get("/api/sessions/{key}/history", session_history),
        web.delete("/api/sessions/{key}", session_clear),
        # Security (new)
        web.get("/api/security/state", security_state),
        web.post("/api/security/mute", security_mute),
        web.delete("/api/security/mute/{caller_id}", security_unmute),
        web.post("/api/security/rate-limit/clear/{caller_id}", security_clear_rate_limit),
        # Config (new)
        web.get("/api/config", config_get),
        # Settings (model config + custom LLMs)
        web.get("/api/settings", settings_get),
        web.post("/api/settings/model", settings_set_model),
        web.delete("/api/settings/daemon-model", settings_clear_daemon_model),
        web.get("/api/settings/custom-models", custom_models_list),
        web.post("/api/settings/custom-models", custom_models_add),
        web.delete("/api/settings/custom-models/{model_id:.+}", custom_models_delete),
    ]
    return routes, _set_live_tracer, _set_reset_callback, _ctrl
