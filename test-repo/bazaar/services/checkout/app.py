"""Application entry point for the checkout service.

Builds the aiohttp application, wires startup and shutdown hooks, and
runs the listener. One process per container, started once at deploy.
"""

import logging

from aiohttp import web

from libs.dbkit.pool import get_pool
from services.checkout import routes
from services.checkout.config import (
    LISTEN_HOST,
    LISTEN_PORT,
    LOG_LEVEL,
    SERVICE_NAME,
    SHUTDOWN_GRACE_S,
)

logger = logging.getLogger(SERVICE_NAME)


async def _on_startup(app):
    # The pool is created once here and shared by every request handler.
    app["pool"] = get_pool()
    logger.info("checkout listening on %s:%d", LISTEN_HOST, LISTEN_PORT)


async def _on_cleanup(app):
    pool = app.get("pool")
    if pool is not None:
        await pool.close()
    logger.info("checkout stopped")


def build_app():
    """Constructs the web application. Called once per process."""
    app = web.Application()
    routes.register(app)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def main():
    logging.basicConfig(level=LOG_LEVEL)
    web.run_app(
        build_app(),
        host=LISTEN_HOST,
        port=LISTEN_PORT,
        shutdown_timeout=SHUTDOWN_GRACE_S,
    )


if __name__ == "__main__":
    main()
