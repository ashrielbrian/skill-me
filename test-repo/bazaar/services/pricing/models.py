"""Event and row shapes used by the pricing worker."""

from dataclasses import dataclass


@dataclass(slots=True)
class PriceEvent:
    """One decoded price or promo event from the stream.

    Built once per stream entry by consumer/decode.py.
    """

    event_id: str
    sku: str
    region: str
    category: str
    currency: str
    amount_cents: int
    promo_code: str
    candidate_skus: tuple
    routing_key: str


@dataclass(slots=True)
class PromoRule:
    """One entry from the promo ruleset."""

    sku: str
    pct_off: int
    code: str
    region: str


@dataclass(slots=True)
class PriceRow:
    """One row of the prices table as written by the apply step."""

    sku: str
    region: str
    base_cents: int
    promo_cents: int
    regional_cents: int
    tax_rate: float

    def as_columns(self):
        """Returns the column tuple in table order."""
        return (
            self.sku,
            self.region,
            self.base_cents,
            self.promo_cents,
            self.regional_cents,
            self.tax_rate,
        )


@dataclass(slots=True)
class CampaignWindow:
    """Active window for one campaign code.

    Returned by campaigns.campaign_window for the admin CLI.
    """

    code: str
    starts_at: str
    ends_at: str
