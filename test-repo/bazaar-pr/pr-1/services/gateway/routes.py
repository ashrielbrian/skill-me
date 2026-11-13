"""Route table for the gateway edge.

The table is built once at import time; `register` attaches it to the
application at process start.
"""
from services.gateway.handlers import (
    health,
    orders_proxy,
    products_proxy,
    session,
    tenants,
)

ROUTES = (
    ("GET", "/healthz", health.liveness),
    ("GET", "/readyz", health.readiness),
    ("GET", "/v1/session", session.whoami),
    ("DELETE", "/v1/session", session.logout),
    ("GET", "/v1/products", products_proxy.list_products),
    ("GET", "/v1/products/{product_id}", products_proxy.get_product),
    ("POST", "/v1/orders", orders_proxy.place_order),
    ("GET", "/v1/orders", orders_proxy.list_orders),
    ("GET", "/v1/orders/{order_id}", orders_proxy.get_order),
    ("POST", "/v1/orders/{order_id}/refund", orders_proxy.refund_order),
    ("GET", "/v1/tenants", tenants.list_tenants),
    ("GET", "/v1/tenants/{tenant_id}", tenants.get_tenant),
    ("GET", "/v1/tenants/{tenant_id}/usage", tenants.get_usage),
)


def register(app):
    """Attaches every public route. Runs once at process start."""
    for method, path, handler in ROUTES:
        app.router.add_route(method, path, handler)
