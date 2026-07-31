"""Static configuration for the settlement batch.

All values are read once at import time.
"""

import os

# Hour of day (UTC) when the nightly run starts.
SETTLEMENT_HOUR_UTC = 2

# Sellers listed in the nightly summary table.
TOP_SELLERS_LIMIT = 50

# Ledger rows written per insert batch.
LEDGER_BATCH_SIZE = 1000

# Rendered reports kept on disk.
RETENTION_DAYS = 30

EXPORT_DIR = os.environ.get("SETTLEMENT_EXPORT_DIR", "/var/spool/settlement")

BANKING_BASE_URL = os.environ.get("BANKING_URL", "https://bank-partner.internal")

REPORT_ENDPOINT = os.environ.get(
    "REPORT_ENDPOINT", "http://gateway.internal:8000/internal/reports"
)
