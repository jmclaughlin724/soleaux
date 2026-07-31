"""Bounded structural reuse (D013): MemoryCache (weighted LRU) and OffCache.

Cache keys bind workspace, source fingerprint, projection, schema version,
extractor version, language, ast-grep version, sgconfig hash, and rule digest.
Entries store only bounded row bytes; restart discards everything.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from soleaux.structural.fragments import (
    EXTRACTOR_VERSION,
    PROJECTION_SCHEMA_VERSION,
)


@dataclass(frozen=True)
class StructuralCacheKey:
    """The exact cache identity for one structural result."""

    workspace_id: str
    source_fingerprint: str
    projection_name: str
    language: str
    sgconfig_hash: str
    rule_digest: str
    # Deliberately has no default: the key already carries `language`, and
    # language-to-analyzer is one-to-one, so one field is unambiguous. A default
    # would silently label a non-ast-grep analyzer's rows with the ast-grep
    # version, which is exactly the stale-cache bug this field exists to prevent.
    analyzer_version: str
    schema_version: str = PROJECTION_SCHEMA_VERSION
    extractor_version: str = EXTRACTOR_VERSION


class MemoryCache:
    """Bounded weighted LRU; the only in-memory reuse implementation."""

    def __init__(self, *, max_entries: int = 2048, max_bytes: int = 128 * 1024 * 1024) -> None:
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[StructuralCacheKey, tuple[bytes, int]] = OrderedDict()
        self._bytes = 0

    def get(self, key: StructuralCacheKey) -> bytes | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        self._entries.move_to_end(key)
        return entry[0]

    def put(self, key: StructuralCacheKey, value: bytes) -> None:
        weight = len(value)
        if weight > self._max_bytes:
            return
        existing = self._entries.pop(key, None)
        if existing is not None:
            self._bytes -= existing[1]
        self._entries[key] = (value, weight)
        self._bytes += weight
        while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
            _, (_, evicted_weight) = self._entries.popitem(last=False)
            self._bytes -= evicted_weight

    def clear(self) -> None:
        """Drop every entry (lifespan exit and restart path)."""
        self._entries.clear()
        self._bytes = 0

    @property
    def size(self) -> tuple[int, int]:
        """(entries, bytes) currently held."""
        return len(self._entries), self._bytes


class OffCache:
    """The no-op reuse implementation."""

    def get(self, key: StructuralCacheKey) -> None:
        return None

    def put(self, key: StructuralCacheKey, value: bytes) -> None:
        return None

    def clear(self) -> None:
        return None
