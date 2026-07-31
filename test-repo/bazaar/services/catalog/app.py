"""Application entry point for the catalog service.

Builds the aiohttp application, opens shared connections on startup,
and closes them on shutdown. Runs once per process start.
"""

import logging

from aiohttp import web

from libs.dbkit.pool import get_pool
from services.catalog import cache, config, routes
from services.catalog.clients import pricing_client
from services.catalog.errors import error_middleware

logger = logging.getLogger(__name__)


async def _on_startup(app: web.Application) -> None:
    app["pool"] = await get_pool(config.DATABASE_DSN)
    app["redis"] = cache.client()
    logger.info("catalog listening on port %d", config.HTTP_PORT)


async def _on_cleanup(app: web.Application) -> None:
    await pricing_client.close()
    await app["redis"].aclose()
    logger.info("catalog stopped")


def create_app() -> web.Application:
    """Assembles the web application. Called once at process start."""
    app = web.Application(middlewares=[error_middleware])
    routes.register(app)
    app.on_startup.append(_on_startup)
    app.on_cleanup.append(_on_cleanup)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    web.run_app(create_app(), port=config.HTTP_PORT)


if __name__ == "__main__":
    main()
