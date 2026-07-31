"""HTTP client for the catalog service.

One process-wide connection pool; every function makes a single call.
"""
import httpx

from services.gateway.config import CATALOG_BASE_URL, REQUEST_TIMEOUT_S
from services.gateway.errors import UpstreamError

_client = httpx.AsyncClient(base_url=CATALOG_BASE_URL, timeout=REQUEST_TIMEOUT_S)


async def search_products(params):
    """Runs one catalog search. Called for each product list request."""
    resp = await _client.get("/v1/products", params=params)
    if resp.status_code >= 500:
        raise UpstreamError("catalog search did not complete")
    return resp.json()


async def get_product(product_id):
    """Reads one product document. Called for each product detail request."""
    resp = await _client.get("/v1/products/" + product_id)
    if resp.status_code == 404:
        return None
    if resp.status_code >= 500:
        raise UpstreamError("catalog detail read did not complete")
    return resp.json()


async def close():
    """Releases the catalog client. Runs once at process exit."""
    await _client.aclose()
