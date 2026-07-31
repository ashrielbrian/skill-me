"""Error types for the inventory service.

The middleware in routes.py maps these onto JSON responses; each class
carries its HTTP status and a stable machine-readable code.
"""

from __future__ import annotations


class InventoryError(Exception):
    """Base class for errors raised by inventory domain code."""

    status = 500
    code = "internal_error"


class ValidationError(InventoryError):
    """Request body failed validation before reaching domain code."""

    status = 400
    code = "invalid_request"


class BatchTooLargeError(ValidationError):
    """Batch reservation asked for more items than RESERVE_BATCH_MAX."""

    code = "batch_too_large"

    def __init__(self, count: int, limit: int) -> None:
        super().__init__(f"batch holds {count} items; the limit is {limit}")
        self.count = count
        self.limit = limit


class UnknownSkuError(InventoryError):
    """Reservation named a SKU with no stock row."""

    status = 404
    code = "unknown_sku"

    def __init__(self, sku: str) -> None:
        super().__init__(f"no stock row for sku {sku}")
        self.sku = sku


class OutOfStockError(InventoryError):
    """Requested quantity is above what the stock row holds."""

    status = 409
    code = "out_of_stock"

    def __init__(self, sku: str, requested: int, available: int) -> None:
        super().__init__(f"sku {sku}: requested {requested}, available {available}")
        self.sku = sku
        self.requested = requested
        self.available = available


def error_body(exc: InventoryError) -> dict:
    """Wire shape shared by every error response."""
    return {"error": {"code": exc.code, "message": str(exc)}}
