"""
Unit tests for cached service wrappers added in Prompt 14.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from app.connectors.cached_connector import CachedDHIS2Connector
from app.connectors.schemas import OrgUnit
from app.core.cache import InMemoryCache, SessionCache
from app.core.session import UserSession, get_session_manager
from app.indicators.cached_calculator import CachedIndicatorCalculator
from app.services.cached_org_units import CachedOrgUnitService
from app.auth.roles import resolve_user_role


@pytest.mark.unit
class TestCachedConnector:
    @pytest.mark.asyncio
    async def test_caches_hierarchy_and_data_values(self) -> None:
        connector = Mock()
        connector.get_org_unit_hierarchy = AsyncMock(return_value=[OrgUnit(uid="ou1", name="Root", level=1)])
        connector.get_data_values = AsyncMock(return_value={"de1": 10.0})
        connector.close = AsyncMock(return_value=None)

        wrapped = CachedDHIS2Connector(
            connector=connector,
            session_id="session-1",
            app_cache=InMemoryCache(max_size=10, default_ttl=60),
            session_cache=SessionCache("session-1", InMemoryCache(max_size=10, default_ttl=60)),
        )

        await wrapped.get_org_unit_hierarchy("ou1")
        await wrapped.get_org_unit_hierarchy("ou1")
        await wrapped.get_data_values(["de1"], "ou1", "202401")
        await wrapped.get_data_values(["de1"], "ou1", "202401")

        assert connector.get_org_unit_hierarchy.call_count == 1
        assert connector.get_data_values.call_count == 1

    @pytest.mark.asyncio
    async def test_can_bypass_connector_cache(self) -> None:
        connector = Mock()
        connector.get_data_values = AsyncMock(return_value={"de1": 10.0})
        connector.close = AsyncMock(return_value=None)

        wrapped = CachedDHIS2Connector(
            connector=connector,
            session_id="session-1",
            app_cache=InMemoryCache(max_size=10, default_ttl=60),
            session_cache=SessionCache("session-1", InMemoryCache(max_size=10, default_ttl=60)),
        )

        await wrapped.get_data_values(["de1"], "ou1", "202401", use_cache=False)
        await wrapped.get_data_values(["de1"], "ou1", "202401", use_cache=False)

        assert connector.get_data_values.call_count == 2


@pytest.mark.unit
class TestCachedCalculator:
    @pytest.mark.asyncio
    async def test_caches_single_and_batch_results(self) -> None:
        calculator = MagicMock()
        calculator._session = MagicMock()
        calculator._population_data = {"ou1": 100}
        calculator.calculate_single = AsyncMock(return_value=MagicMock(indicator_id="VAL-01"))
        calculator.calculate_all = AsyncMock(
            return_value=MagicMock(results=[MagicMock(indicator_id="VAL-01")])
        )

        cache = SessionCache("session-1", InMemoryCache(max_size=10, default_ttl=60))
        wrapped = CachedIndicatorCalculator(calculator, "session-1", cache=cache)

        await wrapped.calculate_single("VAL-01", "ou1", "202401")
        await wrapped.calculate_single("VAL-01", "ou1", "202401")
        await wrapped.calculate_all("ou1", "202401")
        await wrapped.calculate_all("ou1", "202401")

        assert calculator.calculate_single.call_count == 1
        assert calculator.calculate_all.call_count == 1

    @pytest.mark.asyncio
    async def test_setting_expected_pregnancies_invalidates_cache(self) -> None:
        calculator = MagicMock()
        calculator._session = MagicMock()
        calculator._population_data = {}
        calculator.set_expected_pregnancies = MagicMock(side_effect=lambda org_unit, value: calculator._population_data.__setitem__(org_unit, value))
        calculator.calculate_single = AsyncMock(return_value=MagicMock(indicator_id="VAL-01"))

        cache = SessionCache("session-1", InMemoryCache(max_size=10, default_ttl=60))
        wrapped = CachedIndicatorCalculator(calculator, "session-1", cache=cache)

        await wrapped.calculate_single("VAL-01", "ou1", "202401")
        cache.set("dhis2:test", {"rows": []})
        wrapped.set_expected_pregnancies("ou1", 123)
        await wrapped.calculate_single("VAL-01", "ou1", "202401")

        assert calculator.calculate_single.call_count == 2
        assert cache.get("dhis2:test") is None


@pytest.mark.unit
class TestCachedOrgUnitService:
    @pytest.mark.asyncio
    async def test_caches_search_and_roots(self) -> None:
        service = MagicMock()
        service.get_user_roots = AsyncMock(return_value=[MagicMock(uid="ou1")])
        service.search = AsyncMock(return_value=[MagicMock(uid="ou2", name="Mulago")])

        cache = SessionCache("session-1", InMemoryCache(max_size=10, default_ttl=60))
        wrapped = CachedOrgUnitService(service, "session-1", cache=cache)

        await wrapped.get_user_roots()
        await wrapped.get_user_roots()
        await wrapped.search("Mulago")
        await wrapped.search("Mulago")

        assert service.get_user_roots.call_count == 1
        assert service.search.call_count == 1

    @pytest.mark.asyncio
    async def test_can_bypass_org_unit_cache(self) -> None:
        service = MagicMock()
        service.search = AsyncMock(return_value=[MagicMock(uid="ou2", name="Mulago")])

        cache = SessionCache("session-1", InMemoryCache(max_size=10, default_ttl=60))
        wrapped = CachedOrgUnitService(service, "session-1", cache=cache)

        await wrapped.search("Mulago", use_cache=False)
        await wrapped.search("Mulago", use_cache=False)

        assert service.search.call_count == 2


@pytest.mark.unit
class TestSessionCleanup:
    def test_destroy_session_clears_session_cache(self, mock_credentials) -> None:
        manager = get_session_manager()
        manager._sessions.clear()

        now = datetime.now(UTC)
        session = UserSession(
            session_id="cleanup-session",
            created_at=now,
            expires_at=now + timedelta(hours=1),
            credentials=mock_credentials,
        )
        session.user_data["role_info"] = resolve_user_role(
            user_id=mock_credentials.user_id or "user123",
            username=mock_credentials.user_name or "Test User",
            authorities=mock_credentials.authorities,
            org_units=mock_credentials.org_units,
        )
        manager.create_session(session)

        session_cache = SessionCache("cleanup-session", InMemoryCache(max_size=10, default_ttl=60))
        session_cache.set("indicator:test", 1)

        from app.core.cache import get_session_cache

        shared_cache = get_session_cache("cleanup-session")
        shared_cache.set("indicator:test", 1)
        manager.destroy_session("cleanup-session")

        assert shared_cache.get("indicator:test") is None
