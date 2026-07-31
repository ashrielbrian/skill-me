"""Duplicate suppression. seen() runs once per event."""


class Deduper:
    def __init__(self, capacity=50000):
        self._seen = []
        self._capacity = capacity

    def seen(self, event_id):
        if event_id in self._seen:
            return True
        self._seen.append(event_id)
        if len(self._seen) > self._capacity:
            self._seen.pop(0)
        return False

    def size(self):
        return len(self._seen)
