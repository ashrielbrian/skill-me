"""HTTP client for the pricing service.

One shared connection pool serves the whole process; each search
response makes a single batch call here.
"""

import httpx

from services.catalog import config
from services.catalog.errors import UpstreamError

_client = httpx.AsyncClient(
    base_url=config.PRICING_BASE_URL,
    timeout=config.REQUEST_TIMEOUT_S,
)


async def current_prices(product_ids: list[int]) -> list[dict]:
    """Fetches live prices for a batch of ids in one call.

    Runs once per search response.
    """
    response = await _client.post(
        "/v1/prices/batch", json={"product_ids": list(product_ids)}
    )
    if response.status_code != 200:
        raise UpstreamError("pricing returned %d" % response.status_code)
    return response.json()["prices"]


async def close() -> None:
    """Releases the shared pool; the shutdown handler calls this once."""
    await _client.aclose()
