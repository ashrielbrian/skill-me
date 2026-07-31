"""Marketplace-wide limits and defaults.

These values appear in API contracts and event payloads, so they
change only with coordinated releases across the services.
"""

# Order shape limits enforced at the API edge and re-checked in
# checkout before an order row is written.
MAX_LINE_ITEMS = 100
MAX_QUANTITY_PER_LINE = 999

# Canonical SKU length cap after normalization.
MAX_SKU_LENGTH = 64

DEFAULT_REGION = "US"
DEFAULT_CURRENCY = "USD"

# Settlement runs once per day at this UTC hour.
SETTLEMENT_RUN_HOUR_UTC = 2

# Reservation holds release after this many seconds without payment.
RESERVATION_TTL_S = 900

# Outbound webhook budget per delivery attempt.
WEBHOOK_TIMEOUT_S = 10
WEBHOOK_MAX_ATTEMPTS = 6

# Marketplace fee applied to each order total, in basis points.
MARKETPLACE_FEE_BPS = 850

# Largest single order value accepted, in USD cents.
MAX_ORDER_TOTAL_CENTS = 5_000_000

# Search result paging bounds at the catalog API.
SEARCH_PAGE_DEFAULT = 24
SEARCH_PAGE_MAX = 100

# Description length cap at listing time, in characters.
MAX_DESCRIPTION_CHARS = 50_000
