"""Category map mirrored from the catalog service.

Refreshed hourly alongside the ruleset publish; lookups between
refreshes are served from process memory.
"""

import logging

import httpx

from services.pricing.config import CATALOG_BASE_URL

LOG = logging.getLogger(__name__)

_client = httpx.AsyncClient(base_url=CATALOG_BASE_URL, timeout=5.0)

_categories = {}


async def refresh_categories():
    """Reloads the category map from catalog. Runs hourly."""
    resp = await _client.get("/v1/categories")
    resp.raise_for_status()
    payload = resp.json()
    fresh = {item["id"]: item["name"] for item in payload.get("categories", [])}
    _categories.clear()
    _categories.update(fresh)
    LOG.info("category map refreshed: %d entries", len(fresh))
    return len(fresh)


def category_name(category_id):
    """Returns the display name for a category id."""
    return _categories.get(category_id, "general")


async def close():
    """Releases the shared HTTP client at shutdown."""
    await _client.aclose()
