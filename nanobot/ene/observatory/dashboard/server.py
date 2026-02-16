"""Lightweight async HTTP server for the observatory dashboard.

Runs alongside the gateway as an asyncio task. Serves the static
frontend and JSON API endpoints.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from aiohttp import web
from loguru import logger

from nanobot.ene.observatory.dashboard.api import create_api_routes

if TYPE_CHECKING:
    from nanobot.ene.observatory.store import MetricsStore
    from nanobot.ene.observatory.health import HealthMonitor
    from nanobot.ene.observatory.reporter import ReportGenerator


STATIC_DIR = Path(__file__).parent / "static"


class DashboardServer:
    """aiohttp-based dashboard server.

    Serves:
    - Static files (HTML, JS, CSS) from dashboard/static/
    - JSON API endpoints under /api/
    - SSE event stream for real-time updates
    """

    def __init__(
        self,
        store: "MetricsStore",
        health: "HealthMonitor | None" = None,
        reporter: "ReportGenerator | None" = None,
        host: str = "127.0.0.1",
        port: int = 18791,
    ):
        self._store = store
        self._health = health
        self._reporter = reporter
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None
        self._app: web.Application | None = None

    def _create_app(self) -> web.Application:
        """Create the aiohttp application."""
        app = web.Application()

        # CORS middleware (local only, so permissive)
        @web.middleware
        async def cors_middleware(request: web.Request, handler):
            response = await handler(request)
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return response

        app.middlewares.append(cors_middleware)

        # API routes
        api_routes = create_api_routes(
            self._store, self._health, self._reporter
        )
        app.router.add_routes(api_routes)

        # Static files
        if STATIC_DIR.exists():
            # Serve index.html at root
            async def index(request: web.Request) -> web.FileResponse:
                return web.FileResponse(STATIC_DIR / "index.html")

            app.router.add_get("/", index)
            app.router.add_static("/static/", STATIC_DIR, name="static")
        else:
            logger.warning(f"Dashboard static dir not found: {STATIC_DIR}")

        return app

    async def start(self) -> None:
        """Start the dashboard server."""
        self._app = self._create_app()
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()

        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        logger.info(f"Observatory dashboard: http://{self._host}:{self._port}")

    async def stop(self) -> None:
        """Stop the dashboard server."""
        if self._runner:
            await self._runner.cleanup()
            logger.debug("Dashboard server stopped")


async def run_dashboard(
    store: "MetricsStore",
    health: "HealthMonitor | None" = None,
    reporter: "ReportGenerator | None" = None,
    host: str = "127.0.0.1",
    port: int = 18791,
) -> DashboardServer:
    """Create and start a dashboard server. Returns the server instance."""
    server = DashboardServer(store, health, reporter, host, port)
    await server.start()
    return server
