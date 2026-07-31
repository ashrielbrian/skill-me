"""Read-through proxy for the catalog service.

Each handler makes exactly one catalog call and returns the payload in
the standard envelope.
"""
from services.gateway import responses
from services.gateway.clients import catalog_client
from services.gateway.config import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

# Query parameters the edge forwards; everything else is dropped.
_ALLOWED_PARAMS = frozenset({"q", "category", "page", "page_size", "sort"})


def _clean_params(query):
    """Keeps known parameters and clamps page size to the catalog cap."""
    params = {k: v for k, v in query.items() if k in _ALLOWED_PARAMS}
    size = params.get("page_size", "")
    if size.isdigit():
        params["page_size"] = str(min(int(size), MAX_PAGE_SIZE))
    else:
        params["page_size"] = str(DEFAULT_PAGE_SIZE)
    return params


async def list_products(request):
    """Forwards a product search to catalog. Runs for each product list request."""
    payload = await catalog_client.search_products(_clean_params(request.query))
    return responses.envelope(payload)


async def get_product(request):
    """Forwards a product detail read to catalog. Runs for each product detail request."""
    product_id = request.match_info["product_id"]
    payload = await catalog_client.get_product(product_id)
    if payload is None:
        return responses.failure("not_found", "unknown product", 404)
    return responses.envelope(payload)
