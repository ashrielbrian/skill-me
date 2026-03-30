"""Logging configuration."""
import logging
import sys
import json
from datetime import datetime


def setup_logging(level: str = "INFO"):
    """Configure application logging."""
    logger = logging.getLogger("taskflow")
    logger.setLevel(getattr(logging, level))

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    ))
    logger.addHandler(handler)
    return logger


def log_request(method: str, path: str, user_id: int, body: dict = None):
    """Log an API request with details."""
    logger = logging.getLogger("taskflow.requests")
    # BUG [SECURITY]: Logs full request body which may contain passwords
    logger.info(json.dumps({
        "timestamp": datetime.utcnow().isoformat(),
        "method": method,
        "path": path,
        "user_id": user_id,
        "body": body,
    }))


def log_error(error: Exception, context: dict = None):
    """Log an error with context."""
    logger = logging.getLogger("taskflow.errors")
    logger.error(f"Error: {error}", exc_info=True)
    if context:
        # BUG [SECURITY]: Logs full context which may contain sensitive data
        logger.error(f"Context: {json.dumps(context)}")
