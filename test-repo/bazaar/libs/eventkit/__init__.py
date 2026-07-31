"""Redis stream transport shared by every bazaar service.

Producers call ``publish`` and rely on the buffered flush cycle;
consumers call ``consume`` inside their worker loops and ack the
entry ids they handled through ``ack``. ``codec`` fixes the byte
form of every event so services can exchange them without private
agreements, and ``schema`` defines the envelope those bytes must
carry.

The less common helpers stay in their own modules: consumer-group
lifecycle (``groups``), redelivery policy (``retry``), liveness
markers (``heartbeat``), history paging (``replay``), and
counters (``metrics``).
"""

from .codec import decode, encode
from .consumer import consume
from .publisher import flush, publish
from .schema import make_envelope, validate_event
from .streams import stream_name

__all__ = [
    "consume",
    "decode",
    "encode",
    "flush",
    "make_envelope",
    "publish",
    "stream_name",
    "validate_event",
]
