from ..clients.carrier import get_client
from ..money import format_cents
from ..retry import with_retry


def quote(carrier: str, weight_g: int, dest_country: str) -> dict:
    client = get_client()
    amount_cents = with_retry(lambda: client.get_rate(carrier, weight_g, dest_country))
    return {"carrier": carrier, "amount_cents": amount_cents, "display": format_cents(amount_cents)}
