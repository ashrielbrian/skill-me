"""Audit records for money-moving actions.

The audit record schema, including the caller tag, is read by the
settlement export and the operations dashboards. Records are
emitted as single JSON lines on the ``bazaar.audit`` logger, which
the log shipper forwards to the audit store.
"""

import json
import logging
import os
import socket
import traceback
from datetime import datetime, timezone

from .errors import AuditError

_LOGGER = logging.getLogger("bazaar.audit")
_HOST = socket.gethostname()
_PID = os.getpid()

_REQUIRED = frozenset({"action", "actor", "entity"})
_OWN_PATH = os.sep + "commerce" + os.sep


def audit_context() -> dict:
    """Build the shared context fields for one audit record."""
    frames = traceback.extract_stack()
    caller = "unknown"
    # The nearest frame outside this package names the caller tag.
    for frame in reversed(frames[:-1]):
        if _OWN_PATH not in frame.filename:
            caller = "%s:%s" % (os.path.basename(frame.filename), frame.name)
            break
    return {
        "caller": caller,
        "host": _HOST,
        "pid": _PID,
        "at": datetime.now(timezone.utc).isoformat(),
    }


async def write_audit(record):
    """Validate one audit record and emit it as a JSON line."""
    missing = _REQUIRED.difference(record)
    if missing:
        raise AuditError(sorted(missing))
    entry = audit_context()
    entry.update(record)
    line = json.dumps(entry, sort_keys=True, default=str)
    _LOGGER.info("%s", line)
    return entry
