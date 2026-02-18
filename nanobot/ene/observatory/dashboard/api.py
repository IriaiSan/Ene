"""JSON API endpoints for the observatory dashboard.

All endpoints return JSON. Used by the dashboard frontend and
can also be called directly for programmatic access.
"""

from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

from aiohttp import web
from loguru import logger

if TYPE_CHECKING:
    from nanobot.agent.live_trace import LiveTracer
    from nanobot.ene.observatory.store import MetricsStore
    from nanobot.ene.observatory.health import HealthMonitor
    from nanobot.ene.observatory.reporter import ReportGenerator


def create_api_routes(
    store: "MetricsStore",
    health: "HealthMonitor | None" = None,
    reporter: "ReportGenerator | None" = None,
    live_tracer: "LiveTracer | None" = None,
) -> tuple[list[web.RouteDef], Any, Any]:
    """Create all API route definitions.

    Returns:
        Tuple of (routes, set_live_tracer_fn, set_reset_callback_fn).
        The setters allow wiring LiveTracer and the loop reset callback
        after the server has started (modules init later).
    """

    # Mutable container so set_live_tracer() can update after routes are created.
    # The dashboard server starts before AgentLoop initializes modules, so the
    # tracer is None at creation time and gets wired in later.
    _live_ref: list["LiveTracer | None"] = [live_tracer]
    # Async callback that resets agent loop queues/buffers/session. Set by AgentLoop.
    _reset_cb: list[Any] = [None]

    def _set_live_tracer(tracer: "LiveTracer") -> None:
        _live_ref[0] = tracer

    def _set_reset_callback(cb: Any) -> None:
        _reset_cb[0] = cb

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
                # Send summary update every 5 seconds
                summary = store.get_today_summary()
                event_data = json.dumps(summary)
                await response.write(f"event: summary\ndata: {event_data}\n\n".encode())
                await asyncio.sleep(5)
        except (asyncio.CancelledError, ConnectionResetError):
            pass

        return response

    # ── Live Trace endpoints (real-time processing dashboard) ──

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
            # Send catch-up events first
            catchup = tracer.get_events_since(last_id)
            for evt in catchup:
                evt_data = json.dumps(evt)
                await response.write(f"event: event\ndata: {evt_data}\n\n".encode())
                last_id = evt["id"]

            # Send initial state
            state = tracer.get_state()
            state_data = json.dumps(state)
            await response.write(f"event: state\ndata: {state_data}\n\n".encode())

            # Stream new events as they arrive
            while True:
                got_event = await tracer.wait_for_event(timeout=15.0)
                if got_event:
                    new_events = tracer.get_events_since(last_id)
                    for evt in new_events:
                        evt_data = json.dumps(evt)
                        await response.write(f"event: event\ndata: {evt_data}\n\n".encode())
                        last_id = evt["id"]
                else:
                    # Heartbeat — keep connection alive
                    await response.write(b": heartbeat\n\n")

                # Always send fresh state
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
        return web.json_response(tracer.get_state())

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
            # Send catch-up entries
            catchup = tracer.get_prompts_since(last_id)
            for entry in catchup:
                data = json.dumps(entry)
                await response.write(f"event: prompt\ndata: {data}\n\n".encode())
                last_id = entry["id"]

            # Stream new entries as they arrive
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

    async def live_hard_reset(request: web.Request) -> web.Response:
        """Hard reset: drop all queues/buffers, clear session context, wipe tracer.

        Designed for debugging — gives a clean slate without restarting the process.
        Calls the AgentLoop's reset callback (clears debounce buffers, channel queues,
        debounce timers, and invalidates the active session), then resets the LiveTracer.
        """
        tracer = _live_ref[0]
        reset_cb = _reset_cb[0]

        if not tracer:
            return web.json_response({"error": "Live tracer not available"}, status=503)

        # Run the agent loop reset callback (clears queues, buffers, session)
        if reset_cb is not None:
            try:
                import asyncio as _aio
                result = reset_cb()
                if _aio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.warning(f"Hard reset callback error: {e}")

        # Reset the tracer (clears events + state, emits hard_reset event)
        tracer.hard_reset()

        logger.info("Dashboard: hard reset triggered")
        return web.json_response({"ok": True, "message": "Hard reset complete"})

    routes = [
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
        web.get("/api/live", live_stream),
        web.get("/api/live/state", live_state),
        web.get("/api/live/prompts", prompt_stream),
        web.post("/api/live/reset", live_hard_reset),
    ]
    return routes, _set_live_tracer, _set_reset_callback
