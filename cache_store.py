"""Bounded process-local LRU caches. Callers serialize access with their cache lock."""

import time
from collections import OrderedDict


class BoundedCache:
    def __init__(self, max_entries, max_bytes, ttl):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.ttl = ttl
        self.entries = OrderedDict()
        self.bytes = 0

    def clear(self):
        self.entries.clear()
        self.bytes = 0

    def _remove(self, key):
        item = self.entries.pop(key, None)
        if item is not None:
            self.bytes -= item[3]

    def _expire(self, generation):
        now = time.monotonic()
        for key, (created, _, version, _) in list(self.entries.items()):
            if now - created >= self.ttl or version != generation:
                self._remove(key)

    def get(self, key, generation):
        self._expire(generation)
        item = self.entries.get(key)
        if item is None:
            return None
        self.entries.move_to_end(key)
        return item[1]

    def put(self, key, value, generation, size):
        self._expire(generation)
        self._remove(key)
        if size > self.max_bytes:
            return
        self.entries[key] = (time.monotonic(), value, generation, size)
        self.bytes += size
        while len(self.entries) > self.max_entries or self.bytes > self.max_bytes:
            self._remove(next(iter(self.entries)))
