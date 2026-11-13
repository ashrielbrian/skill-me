import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    db_path: str = os.environ.get("PARCELPOST_DB", "parcelpost.db")
    carrier_base_url: str = os.environ.get("CARRIER_BASE_URL", "https://carriers.internal")
    carrier_timeout_s: float = float(os.environ.get("CARRIER_TIMEOUT_S", "5"))


settings = Settings()
