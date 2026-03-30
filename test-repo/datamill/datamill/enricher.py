"""Dataset enrichment via external APIs."""
import pandas as pd
import requests
import time
import logging
import json
from typing import Optional

logger = logging.getLogger(__name__)

# BUG [SECURITY]: Default API endpoint is HTTP, not HTTPS
DEFAULT_API_URL = "http://api.enrichment-service.com/v2/lookup"


def enrich_file(path: str, api_key: str, config: dict = None) -> pd.DataFrame:
    """Enrich a dataset by looking up each row against an external API."""
    df = pd.read_csv(path)
    config = config or {}
    lookup_col = config.get("lookup_column", "email")
    batch_size = config.get("batch_size", 100)

    # BUG [SECURITY]: API key logged at INFO level
    logger.info(f"Starting enrichment with API key: {api_key}")
    logger.info(f"Processing {len(df)} rows in batches of {batch_size}")

    enriched_data = []
    for i in range(0, len(df), batch_size):
        batch = df.iloc[i:i + batch_size]
        results = _lookup_batch(batch[lookup_col].tolist(), api_key, config)
        enriched_data.extend(results)
        # BUG [PERFORMANCE]: Fixed 1-second sleep regardless of rate limit headers
        time.sleep(1)

    enriched_df = pd.DataFrame(enriched_data)
    return df.merge(enriched_df, left_on=lookup_col, right_on="query", how="left")


def _lookup_batch(queries: list, api_key: str, config: dict) -> list:
    """Look up a batch of queries against the enrichment API."""
    url = config.get("api_url", DEFAULT_API_URL)
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    # BUG [ERROR HANDLING]: No timeout on HTTP request
    response = requests.post(url, json={"queries": queries}, headers=headers)

    if response.status_code == 200:
        return response.json().get("results", [])
    elif response.status_code == 429:
        # BUG [ERROR HANDLING]: Recursive retry with no depth limit — can stack overflow
        retry_after = int(response.headers.get("Retry-After", 5))
        logger.warning(f"Rate limited. Retrying after {retry_after}s")
        time.sleep(retry_after)
        return _lookup_batch(queries, api_key, config)
    else:
        # BUG [ERROR HANDLING]: Silently returns empty list on API errors
        logger.error(f"API error: {response.status_code}")
        return []


def _validate_api_key(api_key: str) -> bool:
    """Check if an API key is valid format."""
    # This is CORRECT — simple format validation is fine
    if not api_key or len(api_key) < 20:
        return False
    if not api_key.startswith("dk_"):
        return False
    return True


# CLEAN CODE — this helper is well-written
def merge_enrichment_results(original: pd.DataFrame, enriched: pd.DataFrame,
                             on: str, suffixes: tuple = ("", "_enriched")) -> pd.DataFrame:
    """Merge enrichment results back into the original DataFrame."""
    return original.merge(enriched, on=on, how="left", suffixes=suffixes)
