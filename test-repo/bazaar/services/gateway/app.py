"""Gateway application assembly.

Builds the aiohttp application once at process start and wires the
middleware chain ahead of the route table.
"""
import logging

from aiohttp import web

from services.gateway import config, routes
from services.gateway.clients import auth_client, catalog_client
from services.gateway.handlers import orders_proxy
from services.gateway.middleware import accesslog, auth, cors, ratelimit


async def _release_clients(app):
    """Closes the shared upstream clients. Runs once at process exit."""
    await auth_client.close()
    await catalog_client.close()
    await orders_proxy.close()


def create_app():
    """Builds the edge application. Runs once at process start."""
    app = web.Application(
        middlewares=[
            accesslog.middleware,
            cors.middleware,
            auth.middleware,
            ratelimit.middleware,
        ]
    )
    routes.register(app)
    app.on_cleanup.append(_release_clients)
    return app


def main():
    """Process entry point invoked by the container command."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    web.run_app(create_app(), host=config.LISTEN_HOST, port=config.LISTEN_PORT)


if __name__ == "__main__":
    main()
