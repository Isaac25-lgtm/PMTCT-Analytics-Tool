"""Lightweight mock DHIS2 server for integration tests."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse


class MockDHIS2Handler(BaseHTTPRequestHandler):
    """Serve a small subset of the DHIS2 API used by integration tests."""

    DATA: dict[str, dict[str, dict[str, float]]] = {
        "Q9nSogNmKPt": {"OU_FACILITY_1": {"202401": 150, "202402": 160, "202403": 155}},
        "uALBQG7TFhq": {"OU_FACILITY_1": {"202401": 145, "202402": 158, "202403": 152}},
        "uzlQdD84jNj": {"OU_FACILITY_1": {"202401": 8, "202402": 7, "202403": 9}},
        "C0CnyVY3tm8": {"OU_FACILITY_1": {"202401": 6, "202402": 6, "202403": 7}},
        "ZjQgpP9G7m1": {"OU_FACILITY_1": {"202401": 1, "202402": 1, "202403": 1}},
        "YYhIhKT43kB": {"OU_FACILITY_1": {"202401": 140, "202402": 155, "202403": 150}},
        "bBmnfOD3Yom": {"OU_FACILITY_1": {"202401": 5, "202402": 4, "202403": 6}},
        "GWvftludKFF": {"OU_FACILITY_1": {"202401": 5, "202402": 4, "202403": 5}},
        "aSlLg1v8apE": {"OU_FACILITY_1": {"202401": 130, "202402": 145, "202403": 140}},
        "mhmGSvRwKlQ": {"OU_FACILITY_1": {"202401": 3, "202402": 2, "202403": 4}},
        "fEz9wGsA6YU": {"OU_FACILITY_1": {"202401": 45, "202402": 48, "202403": 50}},
        "yJgPv7Q9lS1": {"OU_FACILITY_1": {"202401": 42, "202402": 46, "202403": 48}},
        "idXOxt69W0e": {"OU_FACILITY_1": {"202401": 50, "202402": 52, "202403": 54}},
        "lQRFuUgxIko": {"OU_FACILITY_1": {"202401": 20, "202402": 22, "202403": 24}},
        "RMbAB4oIe3v": {"OU_FACILITY_1": {"202401": 0, "202402": 1, "202403": 0}},
        "AhfrSeifgVM": {"OU_FACILITY_1": {"202401": 120, "202402": 98, "202403": 85}},
        "ObEurrF8fkT": {"OU_FACILITY_1": {"202401": 1, "202402": 0, "202403": 2}},
        "Y2rG87X018G": {"OU_FACILITY_1": {"202401": 15, "202402": 18, "202403": 21}},
        "uPxx6wu73ZL": {"OU_FACILITY_1": {"202401": 2, "202402": 0, "202403": 1}},
        "FjDiDncMSYs": {"OU_FACILITY_1": {"202401": 90, "202402": 72, "202403": 60}},
        "WjRrKZXi5UA": {"OU_FACILITY_1": {"202401": 0, "202402": 1, "202403": 0}},
        "rr3xZKoBXYt.H9qJO0yGTKz": {"OU_FACILITY_1": {"202401": 2, "202402": 2, "202403": 3}},
        "rr3xZKoBXYt.BaWI6qkhScq": {"OU_FACILITY_1": {"202401": 1, "202402": 1, "202403": 1}},
        "rr3xZKoBXYt.TvOOJYjd3iR": {"OU_FACILITY_1": {"202401": 0, "202402": 0, "202403": 0}},
        "rr3xZKoBXYt.nmvDqGogEyw": {"OU_FACILITY_1": {"202401": 0, "202402": 0, "202403": 0}},
        "rr3xZKoBXYt.fmyu6PJJeQW": {"OU_FACILITY_1": {"202401": 0, "202402": 0, "202403": 0}},
    }

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        """Silence test-server access logging."""
        return

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path == "/api/system/info":
            self._send_json(200, {"version": "2.39", "revision": "mock"})
            return
        if parsed.path == "/api/me":
            self._send_json(
                200,
                {
                    "id": "user123",
                    "username": "integration-user",
                    "displayName": "Integration User",
                    "organisationUnits": [{"id": "OU_FACILITY_1", "name": "Mock Facility"}],
                    "authorities": ["F_EXPORT_DATA", "F_PERFORM_ANALYTICS"],
                },
            )
            return
        if parsed.path == "/api/analytics":
            self._handle_analytics(parse_qs(parsed.query))
            return

        self._send_json(404, {"detail": "Not found"})

    def _handle_analytics(self, params: dict[str, list[str]]) -> None:
        dimensions = params.get("dimension", [])
        dx_items: list[str] = []
        period = None
        org_unit = None

        for dimension in dimensions:
            if dimension.startswith("dx:"):
                dx_items = [item for item in dimension[3:].split(";") if item]
            elif dimension.startswith("pe:"):
                period = dimension[3:]
            elif dimension.startswith("ou:"):
                org_unit = dimension[3:].split(";", 1)[0]

        rows = []
        for dx_item in dx_items:
            value = self.DATA.get(dx_item, {}).get(org_unit or "", {}).get(period or "")
            if value is None:
                continue
            rows.append([dx_item, period, org_unit, str(value)])

        self._send_json(
            200,
            {
                "headers": [
                    {"name": "dx", "column": "Data"},
                    {"name": "pe", "column": "Period"},
                    {"name": "ou", "column": "Organisation unit"},
                    {"name": "value", "column": "Value"},
                ],
                "rows": rows,
                "height": len(rows),
                "width": 4,
            },
        )

    def _send_json(self, status_code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockDHIS2Server:
    """Manage a background HTTP server for integration tests."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0):
        self.host = host
        self.port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("Mock server has not been started")
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self) -> None:
        self._server = ThreadingHTTPServer((self.host, self.port), MockDHIS2Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
