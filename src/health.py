import logging
from collections.abc import Callable

from aiohttp import web

log = logging.getLogger(__name__)


class HealthServer:
    """GET /health → 200 when healthy, 503 otherwise; no data beyond a status flag."""

    def __init__(self, port: int, is_healthy: Callable[[], dict[str, bool]]) -> None:
        self._port = port
        self._is_healthy = is_healthy
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/health", self._health)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        await web.TCPSite(self._runner, host="0.0.0.0", port=self._port).start()
        log.info("health endpoint listening on :%d/health", self._port)

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _health(self, _: web.Request) -> web.Response:
        checks = self._is_healthy()
        healthy = all(checks.values())
        return web.json_response(
            {"status": "ok" if healthy else "unhealthy", **checks}, status=200 if healthy else 503
        )
