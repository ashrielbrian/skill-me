"""Application entry point for the inventory service.

Builds the aiohttp application, warms the shared connection pool, and
schedules the expired-reservation task from reservations/gc.py.
"""

from __future__ import annotations

import asyncio
import logging

from aiohttp import web

from libs.dbkit.pool import get_pool

from services.inventory import config
from services.inventory.reservations.gc import run_forever
from services.inventory.routes import error_middleware, routes

log = logging.getLogger("inventory")


async def _on_startup(app: web.Application) -> None:
    app["pool"] = get_pool()
    app["gc_task"] = asyncio.create_task(run_forever())
    log.info("startup complete; gc runs every %d s", config.GC_INTERVAL_S)


async def _on_cleanup(app: web.Application) -> None:
    task = app["gc_task"]
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def build_app() -> web.Application:
    """Application factory; tests and the entry point share it."""
    app = web.Application(
        middlewares=[error_middleware],
        client_max_size=config.CLIENT_MAX_BODY_BYTES,
    )
    app.add_routes(routes)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def main() -> None:
    logging.basicConfig(
        level=config.LOG_LEVEL,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    web.run_app(
        build_app(),
        port=config.HTTP_PORT,
        shutdown_timeout=config.SHUTDOWN_GRACE_S,
    )


if __name__ == "__main__":
    main()
