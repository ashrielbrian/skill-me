"""Transfer submission to the banking partner.

All of the day's transfers go out in a single batch call; the partner
returns per-transfer results asynchronously over the statement feed.
"""

import logging

import httpx

from services.settlement.config import BANKING_BASE_URL
from services.settlement.errors import BankSubmitError

LOG = logging.getLogger(__name__)

_client = httpx.AsyncClient(base_url=BANKING_BASE_URL, timeout=30.0)


def build_instructions(lines):
    """Returns the transfer payload for the day's payout lines."""
    return [
        {
            "seller_id": line.seller_id,
            "amount_cents": line.net_cents,
            "currency": line.currency,
            "reference": f"settle-{line.day.isoformat()}-{line.seller_id}",
        }
        for line in lines
        if line.net_cents > 0
    ]


async def submit_transfers(lines):
    """Submits all transfers in one batch call. Runs once nightly."""
    instructions = build_instructions(lines)
    if not instructions:
        LOG.info("no transfers to submit")
        return 0
    resp = await _client.post(
        "/v2/transfers/batch", json={"transfers": instructions}
    )
    if resp.status_code >= 400:
        raise BankSubmitError(resp.status_code)
    LOG.info("submitted %d transfers", len(instructions))
    return len(instructions)


async def close():
    """Releases the shared HTTP client at shutdown."""
    await _client.aclose()
