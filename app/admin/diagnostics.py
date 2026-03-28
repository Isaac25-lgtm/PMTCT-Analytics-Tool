"""System diagnostics utilities for the admin dashboard."""

from __future__ import annotations

import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.cache import get_app_cache, get_session_store
from app.core.config import get_settings, normalize_dhis2_base_url
from app.core.connection_pool import get_async_client
from app.core.session import get_session_manager

try:  # pragma: no cover - platform dependent
    import resource
except ImportError:  # pragma: no cover - Windows compatibility
    resource = None


class SystemDiagnostics:
    """Collect lightweight operational diagnostics without persistent storage."""

    async def get_system_status(self) -> dict[str, Any]:
        """Return an admin-friendly snapshot of current system state."""
        settings = get_settings()
        return {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "application": {
                "name": settings.app_name,
                "version": settings.app_version,
                "environment": settings.app_env,
                "debug": settings.debug,
            },
            "runtime": {
                "python_version": platform.python_version(),
                "platform": platform.platform(),
                "processor": platform.processor() or "unknown",
                "cwd": str(Path.cwd()),
                "pid": os.getpid(),
            },
            "memory": self._get_memory_info(),
            "cache": self._get_cache_info(),
            "sessions": self._get_session_info(),
            "configuration": {
                "dhis2_base_url": normalize_dhis2_base_url(settings.dhis2_base_url),
                "dhis2_configured": bool(settings.dhis2_base_url),
                "llm_provider": settings.llm_provider,
                "llm_model": settings.llm_model,
                "llm_configured": bool(settings.llm_api_key),
                "cache_enabled": settings.cache_enabled,
                "rate_limit_enabled": settings.rate_limit_enabled,
                "csrf_enabled": settings.csrf_enabled,
                "audit_enabled": settings.audit_enabled,
            },
        }

    async def check_dhis2_connectivity(self) -> dict[str, Any]:
        """Probe the configured DHIS2 base URL using the shared HTTP client."""
        settings = get_settings()
        if not settings.dhis2_base_url:
            return {"status": "not_configured"}

        client = get_async_client()
        base_url = normalize_dhis2_base_url(settings.dhis2_base_url) or ""
        try:
            response = await client.get(
                f"{base_url}/api/system/info",
                headers={"Accept": "application/json"},
                timeout=settings.http_connect_timeout,
            )
        except Exception as exc:  # pragma: no cover - network dependent
            return {
                "status": "unreachable",
                "base_url": base_url,
                "error": str(exc),
            }

        if response.status_code == 200:
            payload = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
            return {
                "status": "connected",
                "base_url": base_url,
                "version": payload.get("version"),
                "revision": payload.get("revision"),
            }
        if response.status_code in {401, 403}:
            return {
                "status": "reachable_auth_required",
                "base_url": base_url,
                "status_code": response.status_code,
            }
        return {
            "status": "error",
            "base_url": base_url,
            "status_code": response.status_code,
        }

    def _get_memory_info(self) -> dict[str, Any]:
        """Return best-effort process memory usage without extra dependencies."""
        if resource is None:  # pragma: no cover - Windows compatibility
            return {"available": False, "reason": "resource module unavailable"}

        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss_kb = float(usage.ru_maxrss)
        if sys.platform == "darwin":  # pragma: no cover - macOS only
            rss_mb = rss_kb / (1024 * 1024)
        else:
            rss_mb = rss_kb / 1024
        return {
            "available": True,
            "rss_mb": round(rss_mb, 2),
            "user_time_seconds": round(float(usage.ru_utime), 2),
            "system_time_seconds": round(float(usage.ru_stime), 2),
        }

    def _get_cache_info(self) -> dict[str, Any]:
        """Return application and session cache statistics."""
        app_cache = get_app_cache()
        session_store = get_session_store()
        return {
            "application": {
                "enabled": app_cache.enabled,
                "max_size": app_cache.max_size,
                "default_ttl": app_cache.default_ttl,
                "stats": app_cache.stats.to_dict(),
                "namespaces": self._count_namespaces(app_cache.keys()),
            },
            "session_store": {
                "enabled": session_store.enabled,
                "max_size": session_store.max_size,
                "default_ttl": session_store.default_ttl,
                "stats": session_store.stats.to_dict(),
                "namespaces": self._count_session_namespaces(session_store.keys()),
            },
        }

    def _get_session_info(self) -> dict[str, Any]:
        """Return high-level session counts."""
        manager = get_session_manager()
        return {
            "active_sessions": manager.active_session_count,
        }

    def _count_namespaces(self, keys: list[str]) -> dict[str, int]:
        namespaces: dict[str, int] = {}
        for key in keys:
            namespace = key.split(":", 1)[0] if ":" in key else "default"
            namespaces[namespace] = namespaces.get(namespace, 0) + 1
        return namespaces

    def _count_session_namespaces(self, keys: list[str]) -> dict[str, int]:
        namespaces: dict[str, int] = {}
        for key in keys:
            parts = key.split(":", 2)
            namespace = "default"
            if len(parts) == 3 and parts[2]:
                namespace = parts[2].split(":", 1)[0]
            namespaces[namespace] = namespaces.get(namespace, 0) + 1
        return namespaces
