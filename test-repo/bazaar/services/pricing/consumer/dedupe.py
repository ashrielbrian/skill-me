"""Duplicate suppression for stream entries.

Producers retry on ambiguous failures, so the same event id can appear
more than once. A bounded window of recent ids absorbs those repeats.
"""

from collections import OrderedDict


class DedupeWindow:
    """Bounded set of recently seen event ids with insertion order."""

    def __init__(self, capacity):
        self._capacity = capacity
        self._entries = OrderedDict()

    def seen_before(self, event_id):
        """Marks the id as seen; True when it was already present.

        Called once per event.
        """
        if event_id in self._entries:
            self._entries.move_to_end(event_id)
            return True
        self._entries[event_id] = True
        if len(self._entries) > self._capacity:
            self._entries.popitem(last=False)
        return False

    def size(self):
        """Returns the number of ids currently tracked."""
        return len(self._entries)
