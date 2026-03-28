"""
In-memory sliding-window rate limiting.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
import time
from threading import Lock
from typing import Optional

from app.auth.roles import load_rbac_config


class RateLimitOperation(str, Enum):
    AI_INSIGHTS = "ai_insights"
    PDF_EXPORT = "pdf_export"
    EXCEL_EXPORT = "excel_export"
    API_GENERAL = "api_general"
    LOGIN = "login"


@dataclass(frozen=True)
class RateLimitConfig:
    operation: RateLimitOperation
    max_requests: int
    window_seconds: int
    scope: str = "session"


DEFAULT_RATE_LIMITS: dict[RateLimitOperation, RateLimitConfig] = {
    RateLimitOperation.AI_INSIGHTS: RateLimitConfig(
        operation=RateLimitOperation.AI_INSIGHTS,
        max_requests=20,
        window_seconds=3600,
        scope="session",
    ),
    RateLimitOperation.PDF_EXPORT: RateLimitConfig(
        operation=RateLimitOperation.PDF_EXPORT,
        max_requests=30,
        window_seconds=3600,
        scope="session",
    ),
    RateLimitOperation.EXCEL_EXPORT: RateLimitConfig(
        operation=RateLimitOperation.EXCEL_EXPORT,
        max_requests=50,
        window_seconds=3600,
        scope="session",
    ),
    RateLimitOperation.API_GENERAL: RateLimitConfig(
        operation=RateLimitOperation.API_GENERAL,
        max_requests=300,
        window_seconds=60,
        scope="session",
    ),
    RateLimitOperation.LOGIN: RateLimitConfig(
        operation=RateLimitOperation.LOGIN,
        max_requests=5,
        window_seconds=900,
        scope="ip",
    ),
}


@lru_cache
def load_rate_limit_configs() -> dict[RateLimitOperation, RateLimitConfig]:
    """Load rate-limit configs from YAML or fall back to defaults."""
    configured = load_rbac_config().get("rate_limits", {})
    if not configured:
        return dict(DEFAULT_RATE_LIMITS)

    configs: dict[RateLimitOperation, RateLimitConfig] = {}
    for operation_name, payload in configured.items():
        try:
            operation = RateLimitOperation(operation_name)
        except ValueError:
            continue
        configs[operation] = RateLimitConfig(
            operation=operation,
            max_requests=int(payload.get("max_requests", DEFAULT_RATE_LIMITS[operation].max_requests)),
            window_seconds=int(payload.get("window_seconds", DEFAULT_RATE_LIMITS[operation].window_seconds)),
            scope=str(payload.get("scope", DEFAULT_RATE_LIMITS[operation].scope)),
        )

    for operation, config in DEFAULT_RATE_LIMITS.items():
        configs.setdefault(operation, config)
    return configs


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    current_count: int
    limit: int
    remaining: int
    reset_seconds: int
    operation: RateLimitOperation


class RateLimitExceeded(Exception):
    """Raised when a rate limit has been exceeded."""

    def __init__(self, result: RateLimitResult):
        self.result = result
        self.operation = result.operation
        self.retry_after = result.reset_seconds
        super().__init__(
            f"Rate limit exceeded for {result.operation.value}: "
            f"{result.current_count}/{result.limit}, retry after {result.reset_seconds}s"
        )


class RateLimiter:
    """A single-process sliding-window rate limiter."""

    def __init__(self, configs: Optional[dict[RateLimitOperation, RateLimitConfig]] = None):
        self.configs = configs or load_rate_limit_configs()
        self._windows: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
        self._lock = Lock()

    def _scope_key(
        self,
        operation: RateLimitOperation,
        *,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> str:
        config = self.configs.get(operation)
        if not config:
            return "unknown"
        if config.scope == "session" and session_id:
            return f"session:{session_id}"
        if config.scope == "user" and user_id:
            return f"user:{user_id}"
        if config.scope == "ip" and ip_address:
            return f"ip:{ip_address}"
        return session_id or user_id or ip_address or "unknown"

    @staticmethod
    def _clean_window(window: list[float], window_seconds: int) -> list[float]:
        cutoff = time.time() - window_seconds
        return [timestamp for timestamp in window if timestamp > cutoff]

    def check(
        self,
        operation: RateLimitOperation,
        *,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
        increment: bool = True,
    ) -> RateLimitResult:
        config = self.configs.get(operation)
        if config is None:
            return RateLimitResult(
                allowed=True,
                current_count=0,
                limit=0,
                remaining=0,
                reset_seconds=0,
                operation=operation,
            )

        scope_key = self._scope_key(
            operation,
            session_id=session_id,
            user_id=user_id,
            ip_address=ip_address,
        )
        operation_key = operation.value

        with self._lock:
            timestamps = self._clean_window(self._windows[operation_key][scope_key], config.window_seconds)
            self._windows[operation_key][scope_key] = timestamps
            current_count = len(timestamps)
            allowed = current_count < config.max_requests

            if allowed and increment:
                timestamps.append(time.time())
                current_count += 1

            if timestamps:
                oldest = min(timestamps)
                reset_seconds = int(oldest + config.window_seconds - time.time())
            else:
                reset_seconds = config.window_seconds

            return RateLimitResult(
                allowed=allowed,
                current_count=current_count,
                limit=config.max_requests,
                remaining=max(0, config.max_requests - current_count),
                reset_seconds=max(0, reset_seconds),
                operation=operation,
            )

    def reset(
        self,
        operation: RateLimitOperation,
        *,
        session_id: Optional[str] = None,
        user_id: Optional[str] = None,
        ip_address: Optional[str] = None,
    ) -> None:
        scope_key = self._scope_key(
            operation,
            session_id=session_id,
            user_id=user_id,
            ip_address=ip_address,
        )
        with self._lock:
            self._windows.get(operation.value, {}).pop(scope_key, None)

    def cleanup(self) -> int:
        removed = 0
        with self._lock:
            for operation_key, scopes in list(self._windows.items()):
                operation = RateLimitOperation(operation_key)
                config = self.configs.get(operation)
                if config is None:
                    continue
                for scope_key, timestamps in list(scopes.items()):
                    cleaned = self._clean_window(timestamps, config.window_seconds)
                    removed += len(timestamps) - len(cleaned)
                    if cleaned:
                        self._windows[operation_key][scope_key] = cleaned
                    else:
                        del self._windows[operation_key][scope_key]
        return removed


_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter() -> RateLimiter:
    """Return the shared in-memory rate limiter."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter()
    return _rate_limiter
