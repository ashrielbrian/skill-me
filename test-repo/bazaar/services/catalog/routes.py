"""HTTP handlers for the catalog service.

Search composes the normalize, query, rank, facet, and hydrate stages;
detail reads through the Redis cache. Registered once at start.
"""

from aiohttp import web

from services.catalog import (
    cache,
    categories,
    feeds,
    health,
    reviews,
    sellers,
    suggest,
    trending,
)
from services.catalog.errors import NotFoundError, ValidationError
from services.catalog.products import related, repo, views
from services.catalog.search import (
    facets,
    highlight,
    hydrate,
    normalize,
    promoted,
    query,
    rank,
    stopwords,
    synonyms,
)


async def search_handler(request: web.Request) -> web.Response:
    """Handles GET /v1/search. Runs about 300 times per second at peak."""
    raw = request.query.get("q", "")
    if not raw:
        raise ValidationError("q is required")
    terms = normalize.tokenize(normalize.normalize_query(raw))
    terms = stopwords.remove(terms)
    if not terms:
        return web.json_response({"items": [], "facets": {}})
    terms = synonyms.expand(terms)
    category = request.query.get("category")
    tenant = request.headers.get("X-Tenant", "")
    candidates = await query.candidate_rows(terms, category=category)
    promoted_ids = await promoted.get_promoted_ids()
    page = rank.rank_page(candidates, promoted_ids, tenant=tenant)
    page = await hydrate.attach_sellers(page)
    page = await hydrate.attach_prices(page)
    for row in page:
        row["name_html"] = highlight.highlight_name(row["name"], terms)
    counts = facets.facet_counts(candidates)
    facets.annotate_rows(counts, page)
    return web.json_response({"items": page, "facets": facets.summary(counts)})


async def detail_handler(request: web.Request) -> web.Response:
    """Handles GET /v1/products/{id}. Runs about 500 times per second."""
    try:
        product_id = int(request.match_info["product_id"])
    except ValueError:
        raise ValidationError("product id must be an integer")
    cached = await cache.get_detail(product_id)
    if cached is not None:
        return web.json_response(cached)
    row = await repo.get_product(product_id)
    if row is None:
        raise NotFoundError("product %d" % product_id)
    payload = views.detail_view(row)
    payload["reviews"] = await reviews.review_summary(product_id)
    strip = await related.related_products(product_id, row["category"])
    payload["related"] = related.strip_view(strip)
    await cache.set_detail(product_id, payload)
    return web.json_response(payload)


async def suggest_handler(request: web.Request) -> web.Response:
    """Handles GET /v1/suggest for the search box."""
    prefix = request.query.get("q", "")
    if not prefix:
        items = await trending.trending_terms()
        return web.json_response({"suggestions": items})
    items = await suggest.suggestions(prefix)
    return web.json_response({"suggestions": items})


async def health_handler(request: web.Request) -> web.Response:
    """Handles GET /healthz for the load balancer."""
    status = 200 if health.ready() else 503
    return web.json_response(health.health_payload(), status=status)


async def categories_handler(request: web.Request) -> web.Response:
    """Handles GET /v1/categories; serves the nested tree."""
    tree = await categories.category_tree()
    return web.json_response({"categories": tree})


async def seller_handler(request: web.Request) -> web.Response:
    """Handles GET /v1/sellers/{id} for seller profile pages."""
    try:
        seller_id = int(request.match_info["seller_id"])
    except ValueError:
        raise ValidationError("seller id must be an integer")
    card = await sellers.seller_card(seller_id)
    return web.json_response(card)


async def feed_handler(request: web.Request) -> web.Response:
    """Handles GET /v1/feeds/products for the export job."""
    try:
        after_id = int(request.query.get("after", "0"))
    except ValueError:
        raise ValidationError("after must be an integer")
    batch = await feeds.feed_batch(after_id)
    return web.json_response(batch)


def register(app: web.Application) -> None:
    """Attaches all routes. Called once from create_app."""
    app.router.add_get("/v1/search", search_handler)
    app.router.add_get("/v1/products/{product_id}", detail_handler)
    app.router.add_get("/v1/suggest", suggest_handler)
    app.router.add_get("/v1/categories", categories_handler)
    app.router.add_get("/v1/sellers/{seller_id}", seller_handler)
    app.router.add_get("/v1/feeds/products", feed_handler)
    app.router.add_get("/healthz", health_handler)
