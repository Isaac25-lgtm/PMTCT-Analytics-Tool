"""
Unit tests for deployment-oriented structured logging helpers.
"""

from __future__ import annotations

import json
import logging
from unittest.mock import patch

from app.core.logging_config import JSONFormatter, PerformanceLogger, request_id_var


class TestJSONFormatter:
    def test_includes_request_id_and_redacts_sensitive_extra(self) -> None:
        formatter = JSONFormatter()
        token = request_id_var.set("req-1234567890")
        try:
            record = logging.makeLogRecord(
                {
                    "name": "app.test",
                    "levelno": logging.INFO,
                    "levelname": "INFO",
                    "msg": "Structured message",
                    "args": (),
                    "api_key": "secret-value",
                    "path": "/health/live",
                }
            )

            payload = json.loads(formatter.format(record))
        finally:
            request_id_var.reset(token)

        assert payload["message"] == "Structured message"
        assert payload["request_id"] == "req-1234567890"
        assert payload["extra"]["api_key"] == "[REDACTED]"
        assert payload["extra"]["path"] == "/health/live"


class TestPerformanceLogger:
    def test_logs_operation_duration(self) -> None:
        perf = PerformanceLogger("indicator_calculation")

        with patch.object(perf._logger, "info") as mock_info:
            with patch(
                "app.core.logging_config.time.perf_counter",
                side_effect=[10.0, 10.125],
            ):
                duration_ms = perf.start().end({"indicator_count": 30})

        assert round(duration_ms, 2) == 125.0
        mock_info.assert_called_once()
        assert mock_info.call_args.args[0] == "Operation completed"
        assert mock_info.call_args.kwargs["extra"]["operation"] == "indicator_calculation"
        assert mock_info.call_args.kwargs["extra"]["indicator_count"] == 30
