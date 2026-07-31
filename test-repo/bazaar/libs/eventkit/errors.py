"""Error types raised by eventkit helpers.

Consumers catch ``EventkitError`` at the top of their worker loops
and route to the redelivery policy; the precise classes exist for
callers that act on the difference.
"""


class EventkitError(Exception):
    """Base class for every eventkit error."""


class CodecError(EventkitError):
    """An event failed to encode or decode."""


class PublishError(EventkitError):
    """An event could not be queued or delivered."""


class ConsumeError(EventkitError):
    """A consumer read failed for a non-transport reason."""


class GroupError(EventkitError):
    """A consumer-group operation failed."""


class HandlerError(EventkitError):
    """A handler rejected an event it was given.

    Raising this signals the worker loop to run the redelivery
    policy rather than crash the loop.
    """

    def __init__(self, entry_id, reason):
        super().__init__("handler rejected %s: %s" % (entry_id, reason))
        self.entry_id = entry_id
        self.reason = reason
