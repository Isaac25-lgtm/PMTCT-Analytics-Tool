"""
Shared httpx connection pooling for repeated DHIS2 requests.

The live connector already sends absolute URLs and per-request auth headers, so
one shared AsyncClient can safely be reused across sessions without storing
session credentials inside the client.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

from app.core.config import get_settings, load_yaml_config

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ConnectionPoolConfig:
    """Resolved connection-pool settings."""

    max_connections: int
    max_keepalive_connections: int
    keepalive_expiry: float
    connect_timeout: float
    read_timeout: float
    write_timeout: float
    pool_timeout: float

    @classmethod
    def from_settings(cls) -> "ConnectionPoolConfig":
        settings = get_settings()
        try:
            yaml_config = load_yaml_config("cache.yaml") or {}
        except FileNotFoundError:
            yaml_config = {}
        pool = yaml_config.get("connection_pool", {})
        return cls(
            max_connections=int(pool.get("max_connections", settings.http_max_connections)),
            max_keepalive_connections=int(pool.get("max_keepalive", settings.http_max_keepalive)),
            keepalive_expiry=float(pool.get("keepalive_expiry", settings.http_keepalive_expiry)),
            connect_timeout=float(pool.get("connect_timeout", settings.http_connect_timeout)),
            read_timeout=float(pool.get("read_timeout", settings.http_read_timeout)),
            write_timeout=float(pool.get("write_timeout", settings.http_write_timeout)),
            pool_timeout=float(pool.get("pool_timeout", settings.http_pool_timeout)),
        )

    def build_limits(self) -> httpx.Limits:
        return httpx.Limits(
            max_connections=self.max_connections,
            max_keepalive_connections=self.max_keepalive_connections,
            keepalive_expiry=self.keepalive_expiry,
        )

    def build_timeout(self) -> httpx.Timeout:
        return httpx.Timeout(
            connect=self.connect_timeout,
            read=self.read_timeout,
            write=self.write_timeout,
            pool=self.pool_timeout,
        )


_async_client: Optional[httpx.AsyncClient] = None


def get_async_client(config: ConnectionPoolConfig | None = None) -> httpx.AsyncClient:
    """Return the shared pooled AsyncClient."""
    global _async_client
    if _async_client is None:
        resolved = config or ConnectionPoolConfig.from_settings()
        _async_client = httpx.AsyncClient(
            limits=resolved.build_limits(),
            timeout=resolved.build_timeout(),
            http2=True,
            follow_redirects=True,
        )
        logger.info(
            "Created shared httpx client (max_connections=%d, keepalive=%d)",
            resolved.max_connections,
            resolved.max_keepalive_connections,
        )
    return _async_client


async def close_async_client() -> None:
    """Close the shared AsyncClient during app shutdown."""
    global _async_client
    if _async_client is not None:
        await _async_client.aclose()
        _async_client = None
        logger.info("Closed shared httpx client")
