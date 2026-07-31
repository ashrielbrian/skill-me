"""HTTP surface of the inventory service.

Registered routes:
    POST /v1/reserve        reserve stock for one SKU
    POST /v1/reserve/batch  reserve up to RESERVE_BATCH_MAX (100) SKUs per call
    GET  /v1/stock/{sku}    read one stock level
    GET  /healthz           liveness probe
"""

from __future__ import annotations

from aiohttp import web

from services.inventory.errors import (
    InventoryError,
    UnknownSkuError,
    ValidationError,
    error_body,
)
from services.inventory.models import ReserveRequest, parse_batch_payload, validate_sku
from services.inventory.reservations.batch import create_batch
from services.inventory.reservations.create import create_reservation
from services.inventory.stock.repo import get_stock

routes = web.RouteTableDef()


async def _read_json(request: web.Request) -> dict:
    try:
        payload = await request.json()
    except ValueError as exc:
        raise ValidationError("body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValidationError("body must be a JSON object")
    return payload


@routes.post("/v1/reserve")
async def reserve(request: web.Request) -> web.Response:
    """Reserve stock for a single SKU."""
    payload = await _read_json(request)
    reservation = await create_reservation(ReserveRequest.from_payload(payload))
    return web.json_response(reservation.to_wire(), status=201)


@routes.post("/v1/reserve/batch")
async def reserve_batch(request: web.Request) -> web.Response:
    """Reserve up to RESERVE_BATCH_MAX SKUs in one call; see reservations/batch.py."""
    payload = await _read_json(request)
    order_id, zone, items = parse_batch_payload(payload)
    reservations = await create_batch(order_id, zone, items)
    body = {"order_id": order_id, "reservations": [r.to_wire() for r in reservations]}
    return web.json_response(body, status=201)


@routes.get("/v1/stock/{sku}")
async def stock_level(request: web.Request) -> web.Response:
    """Read the current stock level for one SKU."""
    sku = validate_sku(request.match_info["sku"])
    level = await get_stock(sku)
    if level is None:
        raise UnknownSkuError(sku)
    return web.json_response(level.to_wire())


@routes.get("/healthz")
async def healthz(request: web.Request) -> web.Response:
    """Liveness probe for the deploy roller; no database round trip."""
    return web.json_response({"status": "ok", "service": "inventory"})


@web.middleware
async def error_middleware(request: web.Request, handler) -> web.StreamResponse:
    """Map domain errors onto their JSON wire shape."""
    try:
        return await handler(request)
    except InventoryError as exc:
        return web.json_response(error_body(exc), status=exc.status)
