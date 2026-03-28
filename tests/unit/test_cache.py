"""
Unit tests for Prompt 14 cache infrastructure.
"""

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor

import pytest

import app.core.connection_pool as connection_pool_module
from app.core.cache import (
    CacheEntry,
    InMemoryCache,
    SessionCache,
    clear_all_caches,
    clear_session_cache,
    get_app_cache,
    get_session_cache,
)
from app.core.cache_keys import CacheKeys, hash_params, make_key
from app.core.connection_pool import ConnectionPoolConfig, close_async_client, get_async_client


@pytest.mark.unit
class TestCacheEntry:
    def test_entry_expiration(self) -> None:
        entry = CacheEntry(
            key="test",
            value="value",
            created_at=time.time(),
            expires_at=time.time() + 5,
        )
        assert entry.is_expired is False

    def test_touch_updates_metadata(self) -> None:
        entry = CacheEntry(
            key="test",
            value="value",
            created_at=time.time(),
            expires_at=time.time() + 5,
        )
        initial = entry.access_count
        entry.touch()
        assert entry.access_count == initial + 1


@pytest.mark.unit
class TestInMemoryCache:
    @pytest.fixture
    def cache(self) -> InMemoryCache:
        return InMemoryCache(max_size=3, default_ttl=60)

    def test_set_and_get(self, cache: InMemoryCache) -> None:
        cache.set("key1", {"value": 1})
        assert cache.get("key1") == {"value": 1}

    def test_expiration(self, cache: InMemoryCache) -> None:
        cache.set("short", "value", ttl=1)
        assert cache.get("short") == "value"
        time.sleep(1.1)
        assert cache.get("short") is None

    def test_lru_eviction(self, cache: InMemoryCache) -> None:
        cache.set("key1", "value1")
        cache.set("key2", "value2")
        cache.set("key3", "value3")
        assert cache.get("key1") == "value1"
        cache.set("key4", "value4")
        assert cache.get("key2") is None
        assert cache.get("key1") == "value1"

    def test_delete_pattern(self, cache: InMemoryCache) -> None:
        cache.set("indicator:a", 1)
        cache.set("indicator:b", 2)
        cache.set("other:c", 3)
        deleted = cache.delete_pattern("indicator:")
        assert deleted == 2
        assert cache.get("other:c") == 3

    @pytest.mark.asyncio
    async def test_get_or_set_async(self, cache: InMemoryCache) -> None:
        calls = 0

        async def factory() -> str:
            nonlocal calls
            calls += 1
            return "computed"

        assert await cache.get_or_set_async("async", factory) == "computed"
        assert await cache.get_or_set_async("async", factory) == "computed"
        assert calls == 1

    def test_stats_tracking(self, cache: InMemoryCache) -> None:
        cache.set("key1", "value1")
        cache.get("key1")
        cache.get("missing")
        stats = cache.stats
        assert stats.hits == 1
        assert stats.misses == 1

    def test_thread_safety_under_concurrent_access(self) -> None:
        cache = InMemoryCache(max_size=50, default_ttl=60)

        def worker(index: int) -> None:
            key = f"key-{index % 10}"
            cache.set(key, index)
            _ = cache.get(key)

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(worker, range(200)))

        assert len(cache) <= 50


@pytest.mark.unit
class TestSessionCache:
    def test_session_keys_are_prefixed(self) -> None:
        base_cache = InMemoryCache(max_size=10, default_ttl=60)
        session_cache = SessionCache("session-1", base_cache)
        session_cache.set("indicator:test", {"ok": True})
        assert base_cache.get("session:session-1:indicator:test") == {"ok": True}

    def test_clear_session_entries(self) -> None:
        base_cache = InMemoryCache(max_size=10, default_ttl=60)
        session_cache = SessionCache("session-1", base_cache)
        session_cache.set("indicator:test", {"ok": True})
        base_cache.set("metadata:test", {"ok": False})
        assert session_cache.clear() == 1
        assert base_cache.get("metadata:test") == {"ok": False}

    def test_global_session_cache_clear(self) -> None:
        session_cache = get_session_cache("session-x")
        session_cache.set("indicator:test", 1)
        assert clear_session_cache("session-x") == 1


@pytest.mark.unit
class TestCacheKeys:
    def test_hash_params_is_deterministic(self) -> None:
        assert hash_params({"a": 1, "b": 2}) == hash_params({"b": 2, "a": 1})

    def test_hash_params_is_deterministic_for_sets(self) -> None:
        assert hash_params({"uids": {"b", "a"}}) == hash_params({"uids": {"a", "b"}})

    def test_make_key(self) -> None:
        key = make_key("indicator", "single", {"id": "VAL-01"})
        assert key.startswith("indicator:single:")

    def test_domain_specific_keys(self) -> None:
        assert CacheKeys.org_unit_metadata("abc").startswith("orgunit:metadata:")
        assert "indicator:single:" in CacheKeys.indicator_single(
            "VAL-01",
            "ou1",
            "202401",
            None,
            False,
            None,
        )


@pytest.mark.unit
class TestGlobalCaches:
    def test_clear_all_caches(self) -> None:
        get_app_cache().set("metadata:test", 1)
        get_session_cache("session-z").set("indicator:test", 2)
        counts = clear_all_caches()
        assert counts["app"] >= 1
        assert counts["sessions"] >= 1


@pytest.mark.unit
class TestConnectionPool:
    @pytest.mark.asyncio
    async def test_shared_client_is_singleton(self) -> None:
        connection_pool_module._async_client = None
        config = ConnectionPoolConfig(
            max_connections=5,
            max_keepalive_connections=2,
            keepalive_expiry=10,
            connect_timeout=5,
            read_timeout=10,
            write_timeout=5,
            pool_timeout=5,
        )
        client_one = get_async_client(config)
        client_two = get_async_client(config)
        assert client_one is client_two
        await close_async_client()
