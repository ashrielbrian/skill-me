"""Typed row and payload shapes shared across catalog modules.

These are plain data holders; construction happens in the view layer
once per request.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class SearchRequest:
    """Parsed query-string arguments for one search call."""

    terms: tuple[str, ...]
    category: str | None = None
    tenant: str = ""
    page: int = 1


@dataclass(frozen=True)
class ProductSummary:
    """The eight listing fields every search row carries."""

    product_id: int
    name: str
    price_cents: int
    currency: str
    seller_id: int
    category: str
    thumbnail_url: str
    rating: float


@dataclass(frozen=True)
class SellerCard:
    """Seller display fields shown beside a listing."""

    seller_id: int
    display_name: str
    badge: str | None = None


@dataclass(frozen=True)
class FacetBucket:
    """One value/count pair inside a facet group."""

    field: str
    value: str
    count: int


@dataclass
class FeedCursor:
    """Keyset cursor for the product feed; the export job advances it."""

    after_id: int = 0
    done: bool = False


@dataclass(frozen=True)
class PageMeta:
    """Paging block attached to list responses."""

    page: int
    page_size: int
    returned: int
