"""JSON API endpoints for the observatory dashboard.

All endpoints return JSON. Used by the dashboard frontend and
can also be called directly for programmatic access.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from aiohttp import web
from loguru import logger

if TYPE_CHECKING:
    from nanobot.ene.observatory.store import MetricsStore
    from nanobot.ene.observatory.health import HealthMonitor
    from nanobot.ene.observatory.reporter import ReportGenerator


def create_api_routes(
    store: "MetricsStore",
    health: "HealthMonitor | None" = None,
    reporter: "ReportGenerator | None" = None,
) -> list[web.RouteDef]:
    """Create all API route definitions."""

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

    return [
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
    ]
