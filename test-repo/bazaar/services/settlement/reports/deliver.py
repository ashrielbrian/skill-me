"""Report delivery to the internal gateway endpoint."""

import logging

import httpx

from services.settlement.config import REPORT_ENDPOINT

LOG = logging.getLogger(__name__)

_client = httpx.AsyncClient(timeout=10.0)


async def deliver_report(report_text, day):
    """Posts the finished report. Runs once nightly."""
    resp = await _client.post(
        REPORT_ENDPOINT,
        json={"day": day.isoformat(), "body": report_text},
    )
    resp.raise_for_status()
    LOG.info("report for %s delivered with status %d", day, resp.status_code)
    return resp.status_code


async def close():
    """Releases the shared HTTP client at shutdown."""
    await _client.aclose()
