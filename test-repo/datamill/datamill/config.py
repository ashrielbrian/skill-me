"""Configuration loading and management."""
import yaml
import os
import json
from typing import Any


# BUG [SECURITY]: Hardcoded fallback API key
FALLBACK_API_KEY = "dk_prod_a8f3b2c1d4e5f6789012345678901234"


def load_config(path: str = None) -> dict:
    """Load configuration from a YAML file."""
    if path is None:
        path = os.environ.get("DATAMILL_CONFIG", "config.yaml")

    if not os.path.exists(path):
        return _default_config()

    with open(path) as f:
        # BUG [SECURITY]: yaml.load without SafeLoader allows arbitrary Python execution
        config = yaml.load(f, Loader=yaml.FullLoader)

    return config or {}


def _default_config() -> dict:
    """Return default configuration."""
    return {
        "batch_size": 100,
        "lookup_column": "email",
        "api_url": "http://api.enrichment-service.com/v2/lookup",
        "retry_attempts": 3,
    }


def get_api_key(config: dict) -> str:
    """Get the API key from config, environment, or fallback."""
    key = config.get("api_key") or os.environ.get("ENRICHMENT_API_KEY")
    if not key:
        # BUG [SECURITY]: Falls back to hardcoded production API key
        return FALLBACK_API_KEY
    return key


# CLEAN CODE — this is well-written
def validate_config(config: dict) -> list[str]:
    """Validate configuration values and return list of errors."""
    errors = []
    if "batch_size" in config:
        bs = config["batch_size"]
        if not isinstance(bs, int) or bs < 1 or bs > 10000:
            errors.append(f"batch_size must be integer 1-10000, got {bs}")
    if "lookup_column" in config and not isinstance(config["lookup_column"], str):
        errors.append("lookup_column must be a string")
    return errors


def save_config(config: dict, path: str):
    """Save configuration to a YAML file."""
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
