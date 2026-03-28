"""Tests for production startup security guards."""

from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.main import create_app, lifespan


@pytest.fixture(autouse=True)
def clear_settings_cache() -> None:
    """Reset cached settings before and after each test."""
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_production_blocks_placeholder_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "change-me-to-a-random-secret-key")

    app = create_app()

    with pytest.raises(SystemExit):
        async with lifespan(app):
            pass


@pytest.mark.asyncio
async def test_production_allows_real_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("SECRET_KEY", "some-real-random-value")

    app = create_app()

    async with lifespan(app):
        assert app.title


@pytest.mark.asyncio
async def test_development_allows_placeholder_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("SECRET_KEY", "change-me-to-a-random-secret-key")

    app = create_app()

    async with lifespan(app):
        assert app.title
