"""
In-memory caching primitives for the PMTCT application.

The cache is intentionally process-local only:
- no Redis
- no Memcached
- no database-backed state

This keeps Prompt 14 aligned with the stateless MVP while still improving
response times for repeated read-heavy operations.
"""

from __future__ import annotations

import json
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, TypeVar

from app.core.config import get_settings

T = TypeVar("T")


@dataclass(slots=True)
class CacheEntry:
    """One cache entry plus bookkeeping metadata."""

    key: str
    value: Any
    created_at: float
    expires_at: float
    access_count: int = 0
    last_accessed: float = field(default_factory=time.time)
    size_bytes: int = 0

    @property
    def is_expired(self) -> bool:
        return time.time() >= self.expires_at

    @property
    def ttl_remaining(self) -> float:
        return max(0.0, self.expires_at - time.time())

    def touch(self) -> None:
        self.access_count += 1
        self.last_accessed = time.time()


@dataclass(slots=True)
class CacheStats:
    """Basic cache statistics for monitoring and tests."""

    hits: int = 0
    misses: int = 0
    evictions: int = 0
    expirations: int = 0
    total_entries: int = 0
    total_size_bytes: int = 0

    @property
    def hit_rate(self) -> float:
        total = self.hits + self.misses
        return self.hits / total if total else 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate": round(self.hit_rate, 4),
            "evictions": self.evictions,
            "expirations": self.expirations,
            "total_entries": self.total_entries,
            "total_size_bytes": self.total_size_bytes,
        }


class InMemoryCache:
    """Thread-safe in-memory cache with TTL and LRU eviction."""

    def __init__(
        self,
        *,
        max_size: int = 1000,
        default_ttl: int = 300,
        name: str = "default",
        enabled: bool = True,
    ) -> None:
        self.max_size = max(1, int(max_size))
        self.default_ttl = max(1, int(default_ttl))
        self.name = name
        self.enabled = enabled
        self._entries: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()
        self._stats = CacheStats()

    def _estimate_size(self, value: Any) -> int:
        try:
            return len(json.dumps(value, default=str).encode("utf-8"))
        except (TypeError, ValueError):
            return 1024

    def _update_stats(self) -> None:
        self._stats.total_entries = len(self._entries)
        self._stats.total_size_bytes = sum(
            entry.size_bytes for entry in self._entries.values()
        )

    def _prune_expired_locked(self) -> int:
        expired_keys = [
            key for key, entry in self._entries.items() if entry.is_expired
        ]
        for key in expired_keys:
            self._entries.pop(key, None)
            self._stats.expirations += 1
        if expired_keys:
            self._update_stats()
        return len(expired_keys)

    def _evict_lru_locked(self) -> int:
        if not self._entries:
            return 0
        self._entries.popitem(last=False)
        self._stats.evictions += 1
        self._update_stats()
        return 1

    def get(self, key: str) -> Any | None:
        if not self.enabled:
            self._stats.misses += 1
            return None

        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self._stats.misses += 1
                return None

            if entry.is_expired:
                self._entries.pop(key, None)
                self._stats.misses += 1
                self._stats.expirations += 1
                self._update_stats()
                return None

            entry.touch()
            self._entries.move_to_end(key)
            self._stats.hits += 1
            return entry.value

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        if not self.enabled:
            return

        ttl_seconds = max(1, int(ttl if ttl is not None else self.default_ttl))
        now = time.time()
        with self._lock:
            self._prune_expired_locked()
            if key in self._entries:
                self._entries.pop(key, None)

            while len(self._entries) >= self.max_size:
                self._evict_lru_locked()

            entry = CacheEntry(
                key=key,
                value=value,
                created_at=now,
                expires_at=now + ttl_seconds,
                size_bytes=self._estimate_size(value),
            )
            self._entries[key] = entry
            self._entries.move_to_end(key)
            self._update_stats()

    def get_or_set(
        self,
        key: str,
        factory: Callable[[], T],
        ttl: int | None = None,
    ) -> T:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = factory()
        self.set(key, value, ttl)
        return value

    async def get_or_set_async(
        self,
        key: str,
        factory: Callable[[], Any],
        ttl: int | None = None,
    ) -> Any:
        cached = self.get(key)
        if cached is not None:
            return cached
        value = await factory()
        self.set(key, value, ttl)
        return value

    def delete(self, key: str) -> bool:
        with self._lock:
            removed = self._entries.pop(key, None) is not None
            if removed:
                self._update_stats()
            return removed

    def delete_pattern(self, pattern: str) -> int:
        with self._lock:
            matching = [key for key in self._entries.keys() if key.startswith(pattern)]
            for key in matching:
                self._entries.pop(key, None)
            if matching:
                self._update_stats()
            return len(matching)

    def clear(self) -> int:
        with self._lock:
            count = len(self._entries)
            self._entries.clear()
            self._update_stats()
            return count

    def clear_expired(self) -> int:
        with self._lock:
            return self._prune_expired_locked()

    @property
    def stats(self) -> CacheStats:
        with self._lock:
            self._update_stats()
            return CacheStats(
                hits=self._stats.hits,
                misses=self._stats.misses,
                evictions=self._stats.evictions,
                expirations=self._stats.expirations,
                total_entries=self._stats.total_entries,
                total_size_bytes=self._stats.total_size_bytes,
            )

    def keys(self, pattern: str | None = None) -> list[str]:
        with self._lock:
            self._prune_expired_locked()
            if pattern is None:
                return list(self._entries.keys())
            return [key for key in self._entries.keys() if key.startswith(pattern)]

    def __len__(self) -> int:
        with self._lock:
            self._prune_expired_locked()
            return len(self._entries)

    def __contains__(self, key: str) -> bool:
        return self.get(key) is not None


class SessionCache:
    """Session-prefixed wrapper around the shared session cache store."""

    def __init__(self, session_id: str, cache: InMemoryCache) -> None:
        self.session_id = session_id
        self._cache = cache
        self._prefix = f"session:{session_id}:"

    def _make_key(self, key: str) -> str:
        return f"{self._prefix}{key}"

    def get(self, key: str) -> Any | None:
        return self._cache.get(self._make_key(key))

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self._cache.set(self._make_key(key), value, ttl)

    def get_or_set(
        self,
        key: str,
        factory: Callable[[], T],
        ttl: int | None = None,
    ) -> T:
        return self._cache.get_or_set(self._make_key(key), factory, ttl)

    async def get_or_set_async(
        self,
        key: str,
        factory: Callable[[], Any],
        ttl: int | None = None,
    ) -> Any:
        return await self._cache.get_or_set_async(self._make_key(key), factory, ttl)

    def delete(self, key: str) -> bool:
        return self._cache.delete(self._make_key(key))

    def delete_pattern(self, pattern: str) -> int:
        return self._cache.delete_pattern(self._make_key(pattern))

    def clear(self) -> int:
        return self._cache.delete_pattern(self._prefix)

    @property
    def stats(self) -> CacheStats:
        return self._cache.stats


_app_cache: InMemoryCache | None = None
_session_store: InMemoryCache | None = None
_session_wrappers: dict[str, SessionCache] = {}


def get_app_cache() -> InMemoryCache:
    """Return the shared application cache."""
    global _app_cache
    if _app_cache is None:
        settings = get_settings()
        _app_cache = InMemoryCache(
            max_size=settings.cache_max_size,
            default_ttl=settings.cache_default_ttl,
            name="app",
            enabled=settings.cache_enabled,
        )
    return _app_cache


def get_session_store() -> InMemoryCache:
    """Return the shared backing store for all session caches."""
    global _session_store
    if _session_store is None:
        settings = get_settings()
        _session_store = InMemoryCache(
            max_size=settings.cache_max_size,
            default_ttl=settings.cache_default_ttl,
            name="session-store",
            enabled=settings.cache_enabled,
        )
    return _session_store


def get_session_cache(session_id: str) -> SessionCache:
    """Return a session-prefixed cache wrapper."""
    global _session_wrappers
    if session_id not in _session_wrappers:
        _session_wrappers[session_id] = SessionCache(session_id, get_session_store())
    return _session_wrappers[session_id]


def clear_session_cache(session_id: str) -> int:
    """Clear and drop all cache entries for one session."""
    global _session_store, _session_wrappers
    cache = _session_wrappers.pop(session_id, None)
    if cache is None:
        if _session_store is None:
            return 0
        return _session_store.delete_pattern(f"session:{session_id}:")
    return cache.clear()


def clear_all_caches() -> dict[str, int]:
    """Clear both app-wide and session-scoped caches."""
    global _app_cache, _session_store, _session_wrappers
    results = {"app": 0, "sessions": 0}
    if _app_cache is not None:
        results["app"] = _app_cache.clear()
    if _session_store is not None:
        results["sessions"] = _session_store.clear()
    _app_cache = None
    _session_store = None
    _session_wrappers = {}
    return results
