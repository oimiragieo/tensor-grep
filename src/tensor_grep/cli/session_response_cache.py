"""Thread-safe, byte-bounded LRU cache shared by session response paths."""

from __future__ import annotations

import copy
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from tensor_grep.core.config import configured_positive_int

_SESSION_SERVE_RESPONSE_CACHE_MAX_ENTRIES = 32
_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES_ENV = "TENSOR_GREP_SESSION_RESPONSE_CACHE_MAX_BYTES"
_DEFAULT_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES = 8 * 1024 * 1024


@dataclass
class _SessionResponseCacheEntry:
    payload: dict[str, Any]
    size_bytes: int


def _json_size_bytes(payload: dict[str, Any]) -> int:
    return len(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))


class _SessionResponseCacheBase:
    def __init__(
        self,
        max_entries: int = _SESSION_SERVE_RESPONSE_CACHE_MAX_ENTRIES,
        max_size_bytes: int | None = None,
    ) -> None:
        self._max_entries = max(1, max_entries)
        self._max_size_bytes = (
            configured_positive_int(
                _SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES_ENV,
                _DEFAULT_SESSION_SERVE_RESPONSE_CACHE_MAX_BYTES,
            )
            if max_size_bytes is None
            else max(1, int(max_size_bytes))
        )
        self._entries: OrderedDict[tuple[str, ...], _SessionResponseCacheEntry] = OrderedDict()
        self._size_bytes = 0
        self._hits = 0
        self._misses = 0
        self._puts = 0
        self._oversized_skips = 0
        self._lock = threading.RLock()

    def get(self, key: tuple[str, ...]) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is None:
                self._misses += 1
                return None
            self._hits += 1
            self._entries[key] = entry
            return copy.deepcopy(entry.payload)

    def put(self, key: tuple[str, ...], response: dict[str, Any]) -> None:
        with self._lock:
            self._puts += 1
            size_bytes = _json_size_bytes(response)
            if size_bytes > self._max_size_bytes:
                self._oversized_skips += 1
                return
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._size_bytes -= previous.size_bytes
            entry = _SessionResponseCacheEntry(
                payload=copy.deepcopy(response),
                size_bytes=size_bytes,
            )
            self._entries[key] = entry
            self._size_bytes += entry.size_bytes
            while len(self._entries) > self._max_entries or self._size_bytes > self._max_size_bytes:
                _, evicted = self._entries.popitem(last=False)
                self._size_bytes -= evicted.size_bytes

    @property
    def hits(self) -> int:
        with self._lock:
            return self._hits

    @property
    def misses(self) -> int:
        with self._lock:
            return self._misses

    @property
    def puts(self) -> int:
        with self._lock:
            return self._puts

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    @property
    def size_bytes(self) -> int:
        with self._lock:
            return self._size_bytes

    @property
    def max_size_bytes(self) -> int:
        return self._max_size_bytes

    @property
    def oversized_skips(self) -> int:
        with self._lock:
            return self._oversized_skips
