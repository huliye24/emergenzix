"""Bounded, process-local TTL cache for successful market-data responses."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from threading import Lock
from time import monotonic
from typing import Any, Callable, Hashable


class TTLCache:
    """Thread-safe bounded TTL cache.

    Values are copied on read/write so API callers cannot mutate shared cache state.
    Factory exceptions are never cached. A zero TTL disables caching.
    """

    def __init__(
        self,
        *,
        ttl_seconds: float = 30.0,
        max_entries: int = 256,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        if ttl_seconds < 0:
            raise ValueError("ttl_seconds must not be negative")
        if max_entries < 1:
            raise ValueError("max_entries must be at least one")
        self._ttl_seconds = float(ttl_seconds)
        self._max_entries = int(max_entries)
        self._clock = clock
        self._entries: OrderedDict[Hashable, tuple[float, Any]] = OrderedDict()
        self._lock = Lock()

    def get_or_create(self, key: Hashable, factory: Callable[[], Any]) -> Any:
        """Return a fresh cached value or create and cache a successful result."""
        if self._ttl_seconds == 0:
            return factory()

        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None:
                expires_at, value = entry
                if expires_at > now:
                    self._entries.move_to_end(key)
                    return deepcopy(value)
                del self._entries[key]

        # Do not hold the global lock during exchange/network I/O. Concurrent
        # misses may fetch the same key, but unrelated symbols do not block.
        value = factory()

        with self._lock:
            now = self._clock()
            concurrent_entry = self._entries.get(key)
            if concurrent_entry is not None:
                expires_at, cached_value = concurrent_entry
                if expires_at > now:
                    self._entries.move_to_end(key)
                    return deepcopy(cached_value)
                del self._entries[key]

            self._entries[key] = (now + self._ttl_seconds, deepcopy(value))
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

        return deepcopy(value)
