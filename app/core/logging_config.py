"""
Structured logging configuration for development and production.

Production uses JSON logs for easier aggregation on Render.
Development uses a readable console formatter.
"""

from __future__ import annotations

import json
import logging
import logging.config
import time
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any, Optional

request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)

_DEFAULT_RECORD = logging.LogRecord(
    name="root",
    level=logging.INFO,
    pathname=__file__,
    lineno=0,
    msg="",
    args=(),
    exc_info=None,
)
_RESERVED_LOG_FIELDS = set(_DEFAULT_RECORD.__dict__.keys())


class JSONFormatter(logging.Formatter):
    """Render log records as structured JSON."""

    SENSITIVE_FIELDS = {
        "password",
        "token",
        "secret",
        "key",
        "authorization",
        "cookie",
        "credentials",
        "pat_token",
        "api_key",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id

        extra: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _RESERVED_LOG_FIELDS or key == "request_id" or key.startswith("_"):
                continue
            extra[key] = "[REDACTED]" if self._is_sensitive(key) else value

        if extra:
            payload["extra"] = extra

        if record.exc_info:
            payload["exception"] = {
                "type": record.exc_info[0].__name__ if record.exc_info[0] else None,
                "message": str(record.exc_info[1]) if record.exc_info[1] else None,
                "traceback": "".join(traceback.format_exception(*record.exc_info)),
            }

        return json.dumps(payload, default=str)

    def _is_sensitive(self, key: str) -> bool:
        lowered = key.lower()
        return any(fragment in lowered for fragment in self.SENSITIVE_FIELDS)


class ConsoleFormatter(logging.Formatter):
    """Readable formatter for local development."""

    def format(self, record: logging.LogRecord) -> str:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        request_id = request_id_var.get()
        request_suffix = f" [{request_id[:8]}]" if request_id else ""
        message = (
            f"{timestamp} {record.levelname:8} "
            f"[{record.name}]{request_suffix} {record.getMessage()}"
        )
        if record.exc_info:
            message += "\n" + "".join(traceback.format_exception(*record.exc_info))
        return message


def configure_logging(*, level: str = "INFO", format: str = "console") -> None:
    """Configure application logging."""

    formatter_name = "json" if format.lower() == "json" else "console"
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "json": {"()": JSONFormatter},
                "console": {"()": ConsoleFormatter},
            },
            "handlers": {
                "stdout": {
                    "class": "logging.StreamHandler",
                    "formatter": formatter_name,
                    "stream": "ext://sys.stdout",
                }
            },
            "loggers": {
                "app": {
                    "level": level.upper(),
                    "handlers": ["stdout"],
                    "propagate": False,
                },
                "audit": {
                    "level": "INFO",
                    "handlers": ["stdout"],
                    "propagate": False,
                },
                "uvicorn": {
                    "level": level.upper(),
                    "handlers": ["stdout"],
                    "propagate": False,
                },
                "uvicorn.access": {
                    "level": "INFO",
                    "handlers": ["stdout"],
                    "propagate": False,
                },
                "httpx": {
                    "level": "WARNING",
                    "handlers": ["stdout"],
                    "propagate": False,
                },
                "httpcore": {
                    "level": "WARNING",
                    "handlers": ["stdout"],
                    "propagate": False,
                },
            },
            "root": {
                "level": "WARNING",
                "handlers": ["stdout"],
            },
        }
    )


class RequestLogger:
    """Helper used by middleware to log request lifecycle events."""

    def __init__(self) -> None:
        self._logger = logging.getLogger("app.request")

    def log_request(
        self,
        *,
        method: str,
        path: str,
        request_id: str,
        client_ip: str | None = None,
    ) -> None:
        self._logger.info(
            "Request started",
            extra={
                "method": method,
                "path": path,
                "request_id": request_id,
                "client_ip": client_ip,
            },
        )

    def log_response(
        self,
        *,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        request_id: str,
    ) -> None:
        level = logging.INFO if status_code < 400 else logging.WARNING
        self._logger.log(
            level,
            "Request completed",
            extra={
                "method": method,
                "path": path,
                "status_code": status_code,
                "duration_ms": round(duration_ms, 2),
                "request_id": request_id,
            },
        )


class PerformanceLogger:
    """Lightweight timing helper for structured performance logs."""

    def __init__(self, operation: str) -> None:
        self.operation = operation
        self._logger = logging.getLogger("app.performance")
        self._start_time: float | None = None

    def start(self) -> "PerformanceLogger":
        """Start timing and return self for fluent usage."""
        self._start_time = time.perf_counter()
        return self

    def end(self, extra: dict[str, Any] | None = None) -> float:
        """Stop timing, emit a log, and return elapsed milliseconds."""
        if self._start_time is None:
            return 0.0

        duration_ms = (time.perf_counter() - self._start_time) * 1000
        log_extra: dict[str, Any] = {
            "operation": self.operation,
            "duration_ms": round(duration_ms, 2),
        }
        if extra:
            log_extra.update(extra)

        self._logger.info("Operation completed", extra=log_extra)
        return duration_ms

    def __enter__(self) -> "PerformanceLogger":
        return self.start()

    def __exit__(self, *args: object) -> None:
        self.end()
