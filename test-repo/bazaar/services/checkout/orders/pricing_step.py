"""Pricing step for order placement.

Resolves the unit price and tax rate for each line item before totals
are computed. Prices live in Postgres in the seller's currency; orders
settle in USD.
"""

from dataclasses import replace

from libs.commerce.money import convert
from libs.commerce.tax import rate_for
from libs.dbkit.executor import fetch_one
from services.checkout.errors import PricingFailure

_PRICE_QUERY = "SELECT price_cents, currency FROM prices WHERE product_id = $1"


async def price_items(items, buyer_region):
    """Prices every line item on one order.

    Runs once per order; an order averages 8 line items.
    """
    priced = []
    for item in items:
        row = await fetch_one(_PRICE_QUERY, (item.product_id,))
        if row is None:
            raise PricingFailure(item.product_id)
        unit_price_cents = convert(row["price_cents"], row["currency"], "USD")
        tax_rate = rate_for(buyer_region, item.category)
        priced.append(
            replace(item, unit_price_cents=unit_price_cents, tax_rate=tax_rate)
        )
    return priced
