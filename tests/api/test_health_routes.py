"""
API tests for health and system-status routes.
"""

from __future__ import annotations

import pytest


@pytest.mark.api
class TestHealthRoutes:
    def test_health_check(self, client) -> None:
        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "environment" in data
        assert "checks" in data

    def test_liveness_probe(self, client) -> None:
        response = client.get("/health/live")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

    def test_readiness_probe(self, client) -> None:
        response = client.get("/health/ready")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert data["ready"] is True
        assert "checks" in data

    def test_startup_probe(self, client) -> None:
        response = client.get("/health/startup")

        assert response.status_code == 200
        assert response.json()["status"] == "started"

    def test_cache_status(self, client) -> None:
        response = client.get("/health/cache")

        assert response.status_code == 200
        data = response.json()
        assert "enabled" in data
        assert "hit_rate" in data

    def test_system_stats(self, client) -> None:
        response = client.get("/health/stats")

        assert response.status_code == 200
        data = response.json()
        assert "app" in data
        assert "sessions" in data
        assert "indicators" in data
        assert "cache" in data

    def test_request_id_header_is_echoed(self, client) -> None:
        response = client.get(
            "/health/live",
            headers={"X-Request-ID": "deployment-test-request-id"},
        )

        assert response.status_code == 200
        assert response.headers["x-request-id"] == "deployment-test-request-id"
