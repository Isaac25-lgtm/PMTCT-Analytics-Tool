"""
Shared pytest fixtures for the PMTCT Triple Elimination test suite.

These fixtures align with the actual app interfaces from Prompts 2-7.
They validate the real application rather than redefining it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncGenerator, Generator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import app.api.deps as deps
import app.core.session as session_module
import app.auth.rate_limit as rate_limit_module
import app.core.cache as cache_module
import app.core.connection_pool as connection_pool_module
from app.auth.roles import resolve_user_role
from app.core.session import AuthMethod, DHIS2Credentials, SessionManager, UserSession
from app.indicators.models import (
    FormulaComponent,
    IndicatorCategory,
    IndicatorDefinition,
    IndicatorResult,
    IndicatorResultSet,
    Periodicity,
    ResultType,
)
from app.indicators.registry import IndicatorRegistry
from app.main import create_app

TEST_ORG_UNIT = "akV6429SUqu"
TEST_ORG_UNIT_NAME = "Test District"
TEST_PERIOD = "202401"
TEST_BASE_URL = "https://test.dhis2.org"


def build_result(
    indicator_id: str,
    indicator_name: str,
    category: IndicatorCategory,
    *,
    period: str = TEST_PERIOD,
    result_value: float | None = None,
    numerator_value: float | None = None,
    denominator_value: float | None = None,
    result_type: ResultType = ResultType.PERCENTAGE,
    target: float | None = None,
    meets_target: bool | None = None,
    is_valid: bool = True,
    error_message: str | None = None,
    data_elements_used: dict[str, float | None] | None = None,
) -> IndicatorResult:
    """Build a realistic indicator result for tests."""
    return IndicatorResult(
        indicator_id=indicator_id,
        indicator_name=indicator_name,
        category=category,
        org_unit_uid=TEST_ORG_UNIT,
        org_unit_name=TEST_ORG_UNIT_NAME,
        period=period,
        numerator_value=numerator_value,
        denominator_value=denominator_value,
        result_value=result_value,
        result_type=result_type,
        target=target,
        meets_target=meets_target,
        is_valid=is_valid,
        error_message=error_message,
        data_elements_used=data_elements_used or {},
    )


@pytest.fixture
def mock_credentials() -> DHIS2Credentials:
    """Create PAT credentials for authenticated tests."""
    return DHIS2Credentials(
        base_url=TEST_BASE_URL,
        auth_method=AuthMethod.PAT,
        pat_token="test_pat_token_12345",
        user_id="user123",
        user_name="Test User",
        authorities=["F_EXPORT_DATA", "F_PERFORM_ANALYTICS", "F_DATAVALUE_ADD"],
        org_units=[
            {"id": TEST_ORG_UNIT, "name": TEST_ORG_UNIT_NAME, "level": 3},
            {"id": "child123", "name": "Test Facility", "level": 4},
        ],
    )


@pytest.fixture
def mock_credentials_basic() -> DHIS2Credentials:
    """Create basic-auth credentials for authentication tests."""
    return DHIS2Credentials(
        base_url=TEST_BASE_URL,
        auth_method=AuthMethod.BASIC,
        username="testuser",
        password="testpass",
        user_id="user456",
        user_name="Basic User",
        authorities=["F_EXPORT_DATA"],
        org_units=[{"id": "ou789", "name": "Another District", "level": 3}],
    )


@pytest.fixture
def valid_session(mock_credentials: DHIS2Credentials) -> UserSession:
    """Create a valid, non-expired user session."""
    now = datetime.now(UTC)
    session = UserSession(
        session_id="test_session_123",
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
    session.user_data["csrf_token"] = "test-csrf-token"
    return session


@pytest.fixture
def admin_session(mock_credentials: DHIS2Credentials) -> UserSession:
    """Create an admin-level session for privileged route tests."""
    now = datetime.now(UTC)
    credentials = DHIS2Credentials(
        base_url=mock_credentials.base_url,
        auth_method=mock_credentials.auth_method,
        pat_token=mock_credentials.pat_token,
        user_id="admin123",
        user_name="Admin User",
        authorities=["ALL", "F_SYSTEM_SETTING", "M_dhis-web-settings"],
        org_units=mock_credentials.org_units,
    )
    session = UserSession(
        session_id="admin_session_123",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        credentials=credentials,
    )
    session.user_data["role_info"] = resolve_user_role(
        user_id=credentials.user_id or "admin123",
        username=credentials.user_name or "Admin User",
        authorities=credentials.authorities,
        org_units=credentials.org_units,
    )
    session.user_data["csrf_token"] = "admin-csrf-token"
    return session


@pytest.fixture
def expired_session(mock_credentials: DHIS2Credentials) -> UserSession:
    """Create an expired user session."""
    now = datetime.now(UTC)
    return UserSession(
        session_id="expired_session_456",
        created_at=now - timedelta(hours=2),
        expires_at=now - timedelta(hours=1),
        credentials=mock_credentials,
    )


@pytest.fixture
def fresh_session_manager(monkeypatch: pytest.MonkeyPatch) -> SessionManager:
    """Replace the global session manager with a fresh isolated instance."""
    manager = SessionManager(timeout_minutes=60)
    monkeypatch.setattr(session_module, "_session_manager", manager)
    return manager


@pytest.fixture
def fresh_registry() -> IndicatorRegistry:
    """Create a fresh registry instance by resetting the singleton."""
    IndicatorRegistry._instance = None
    IndicatorRegistry._initialized = False
    return IndicatorRegistry()


@pytest.fixture
def minimal_mappings_yaml(tmp_path: Path) -> Path:
    """Create a minimal mappings.yaml aligned to the test indicators."""
    content = """
data_elements:
  AN01a: Q9nSogNmKPt
  AN17a: uALBQG7TFhq
  AN14b: bBmnfOD3Yom
  AN14c: GWvftludKFF
  SS40a: kitConsume001
  SS40c: kitStock001
  "033B-AP04": weeklyExpected001
  "033B-AP05": weeklyMissed001

an21_pos_cocs:
  - H9qJO0yGTKz
  - BaWI6qkhScq
"""
    file_path = tmp_path / "mappings.yaml"
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.fixture
def minimal_indicators_yaml(tmp_path: Path) -> Path:
    """Create a minimal indicators.yaml for registry and trend tests."""
    content = """
indicators:
  VAL-02:
    id: "VAL-02"
    name: "HIV Testing Coverage at ANC"
    category: "who_validation"
    description: "Percentage of ANC attendees tested for HIV"
    numerator:
      formula: "AN17a"
      label: "Pregnant women tested for HIV"
    denominator:
      formula: "AN01a"
      label: "ANC 1st visits"
    result_type: "percentage"
    target: 95.0
    periodicity: "monthly"

  VAL-05:
    id: "VAL-05"
    name: "Syphilis Treatment Coverage"
    category: "who_validation"
    description: "Percentage of syphilis-positive pregnant women treated"
    numerator:
      formula: "AN14c"
      label: "Syphilis treated"
    denominator:
      formula: "AN14b"
      label: "Syphilis positive"
    result_type: "percentage"
    target: 95.0
    periodicity: "monthly"

  SUP-01:
    id: "SUP-01"
    name: "HBsAg Kits Consumed"
    category: "supply"
    description: "Count of HBsAg test kits consumed"
    numerator:
      formula: "SS40a"
      label: "Kits consumed"
    result_type: "count"
    periodicity: "monthly"

  SUP-05:
    id: "SUP-05"
    name: "HBsAg Days of Use"
    category: "supply"
    description: "Days of use for HBsAg stock"
    calculation_type: "days_of_use"
    stock_on_hand: "SS40c"
    consumption: "SS40a"
    result_type: "days"
    periodicity: "monthly"

  SYS-03:
    id: "SYS-03"
    name: "Missed Appointment Rate"
    category: "system"
    description: "Weekly missed appointments"
    numerator:
      formula: "033B-AP05"
      label: "Missed"
    denominator:
      formula: "033B-AP04"
      label: "Expected"
    result_type: "percentage"
    periodicity: "weekly"
"""
    file_path = tmp_path / "indicators.yaml"
    file_path.write_text(content, encoding="utf-8")
    return file_path


@pytest.fixture
def loaded_registry(
    fresh_registry: IndicatorRegistry,
    minimal_mappings_yaml: Path,
    minimal_indicators_yaml: Path,
) -> IndicatorRegistry:
    """Return a registry loaded with minimal test fixtures."""
    fresh_registry.load(
        indicators_path=str(minimal_indicators_yaml),
        mappings_path=str(minimal_mappings_yaml),
    )
    return fresh_registry


@pytest.fixture
def sample_percentage_indicator() -> IndicatorDefinition:
    """A monthly percentage indicator definition."""
    return IndicatorDefinition(
        id="VAL-02",
        name="HIV Testing Coverage at ANC",
        category=IndicatorCategory.WHO_VALIDATION,
        description="Percentage of ANC attendees tested for HIV",
        numerator=FormulaComponent(formula="AN17a", label="Pregnant women tested for HIV"),
        denominator=FormulaComponent(formula="AN01a", label="ANC 1st visits"),
        result_type=ResultType.PERCENTAGE,
        target=95.0,
        periodicity=Periodicity.MONTHLY,
    )


@pytest.fixture
def sample_count_indicator() -> IndicatorDefinition:
    """A monthly count indicator definition."""
    return IndicatorDefinition(
        id="SUP-01",
        name="HBsAg Kits Consumed",
        category=IndicatorCategory.SUPPLY,
        description="Count of HBsAg test kits consumed",
        numerator=FormulaComponent(formula="SS40a", label="Kits consumed"),
        result_type=ResultType.COUNT,
        periodicity=Periodicity.MONTHLY,
    )


@pytest.fixture
def sample_weekly_indicator() -> IndicatorDefinition:
    """A weekly indicator definition used for trend exclusion tests."""
    return IndicatorDefinition(
        id="SYS-03",
        name="Missed Appointment Rate",
        category=IndicatorCategory.SYSTEM,
        description="Weekly missed appointments",
        numerator=FormulaComponent(formula="033B-AP05", label="Missed"),
        denominator=FormulaComponent(formula="033B-AP04", label="Expected"),
        result_type=ResultType.PERCENTAGE,
        periodicity=Periodicity.WEEKLY,
    )


@pytest.fixture
def sample_valid_result() -> IndicatorResult:
    """A valid percentage result that meets its target."""
    return build_result(
        "VAL-02",
        "HIV Testing Coverage at ANC",
        IndicatorCategory.WHO_VALIDATION,
        result_value=95.0,
        numerator_value=950.0,
        denominator_value=1000.0,
        target=95.0,
        meets_target=True,
        data_elements_used={"AN17a": 950.0, "AN01a": 1000.0},
    )


@pytest.fixture
def sample_invalid_result() -> IndicatorResult:
    """An invalid result representing missing data."""
    return build_result(
        "VAL-05",
        "Syphilis Treatment Coverage",
        IndicatorCategory.WHO_VALIDATION,
        result_type=ResultType.PERCENTAGE,
        target=95.0,
        is_valid=False,
        error_message="Missing data",
    )


@pytest.fixture
def sample_result_set(
    sample_valid_result: IndicatorResult,
    sample_invalid_result: IndicatorResult,
) -> IndicatorResultSet:
    """A small WHO validation result set."""
    result_set = IndicatorResultSet(
        org_unit_uid=TEST_ORG_UNIT,
        org_unit_name=TEST_ORG_UNIT_NAME,
        period=TEST_PERIOD,
    )
    result_set.add_result(sample_valid_result)
    result_set.add_result(sample_invalid_result)
    return result_set


@pytest.fixture
def cascade_result_set() -> IndicatorResultSet:
    """A minimal HIV cascade result set."""
    result_set = IndicatorResultSet(
        org_unit_uid=TEST_ORG_UNIT,
        org_unit_name=TEST_ORG_UNIT_NAME,
        period=TEST_PERIOD,
    )
    result_set.add_result(
        build_result(
            "HIV-01",
            "Known HIV Status",
            IndicatorCategory.HIV_CASCADE,
            result_value=88.0,
            numerator_value=880.0,
            denominator_value=1000.0,
        )
    )
    result_set.add_result(
        build_result(
            "HIV-02",
            "HIV Positivity",
            IndicatorCategory.HIV_CASCADE,
            result_value=3.2,
            numerator_value=32.0,
            denominator_value=1000.0,
        )
    )
    return result_set


@pytest.fixture
def supply_result_set() -> IndicatorResultSet:
    """A supply-chain result set matching the current route contract."""
    result_set = IndicatorResultSet(
        org_unit_uid=TEST_ORG_UNIT,
        org_unit_name=TEST_ORG_UNIT_NAME,
        period=TEST_PERIOD,
    )
    result_set.add_result(
        build_result(
            "SUP-01",
            "HBsAg Kits Consumed",
            IndicatorCategory.SUPPLY,
            result_value=12.0,
            numerator_value=12.0,
            result_type=ResultType.COUNT,
        )
    )
    result_set.add_result(
        build_result(
            "SUP-02",
            "HBsAg Stockout Days",
            IndicatorCategory.SUPPLY,
            result_value=0.0,
            numerator_value=0.0,
            result_type=ResultType.DAYS,
        )
    )
    result_set.add_result(
        build_result(
            "SUP-03",
            "Duo Kits Consumed",
            IndicatorCategory.SUPPLY,
            result_value=10.0,
            numerator_value=10.0,
            result_type=ResultType.COUNT,
        )
    )
    result_set.add_result(
        build_result(
            "SUP-04",
            "Duo Stockout Days",
            IndicatorCategory.SUPPLY,
            result_value=2.0,
            numerator_value=2.0,
            result_type=ResultType.DAYS,
        )
    )
    result_set.add_result(
        build_result(
            "SUP-05",
            "HBsAg Days of Use",
            IndicatorCategory.SUPPLY,
            result_value=45.0,
            numerator_value=300.0,
            denominator_value=6.7,
            result_type=ResultType.DAYS,
        )
    )
    result_set.add_result(
        build_result(
            "SUP-06",
            "Duo Days of Use",
            IndicatorCategory.SUPPLY,
            result_value=9.0,
            numerator_value=120.0,
            denominator_value=13.3,
            result_type=ResultType.DAYS,
        )
    )
    return result_set


@pytest.fixture
def mock_connector() -> AsyncMock:
    """Create a mocked DHIS2 connector that works as an async context manager."""
    connector = AsyncMock()
    connector.get_data_values = AsyncMock(
        return_value={
            "Q9nSogNmKPt": 1000.0,
            "uALBQG7TFhq": 950.0,
            "bBmnfOD3Yom": 50.0,
            "GWvftludKFF": 48.0,
            "kitConsume001": 90.0,
            "kitStock001": 300.0,
        }
    )
    connector.get_an21_pos_total = AsyncMock(return_value=15.0)
    connector.__aenter__ = AsyncMock(return_value=connector)
    connector.__aexit__ = AsyncMock(return_value=None)
    return connector


@pytest.fixture
def mock_calculator(
    sample_valid_result: IndicatorResult,
    sample_result_set: IndicatorResultSet,
) -> MagicMock:
    """Create a mock calculator aligned to the current Prompt 3 API."""
    calculator = MagicMock()
    calculator.calculate_all = AsyncMock(return_value=sample_result_set)
    calculator.calculate_single = AsyncMock(return_value=sample_valid_result)
    calculator.set_expected_pregnancies = MagicMock()
    calculator.clear_expected_pregnancies = MagicMock()
    return calculator


@pytest.fixture
def population_data() -> dict[str, int]:
    """Population data used in calculator tests."""
    return {
        TEST_ORG_UNIT: 25000,
        "child123": 5000,
    }


@pytest.fixture
def app(fresh_session_manager: SessionManager):
    """Create a FastAPI app instance for each test."""
    rate_limit_module._rate_limiter = None
    cache_module._app_cache = None
    cache_module._session_store = None
    cache_module._session_wrappers = {}
    connection_pool_module._async_client = None
    return create_app()


@pytest.fixture
def client(app) -> Generator[TestClient, None, None]:
    """Create a synchronous test client."""
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def async_client(app) -> AsyncGenerator[AsyncClient, None]:
    """Create an async test client."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        yield http_client


@pytest.fixture
def authenticated_client(
    app,
    valid_session: UserSession,
    fresh_session_manager: SessionManager,
) -> Generator[TestClient, None, None]:
    """Create a client with a real in-memory session cookie."""
    fresh_session_manager.create_session(valid_session)
    with TestClient(app) as test_client:
        test_client.cookies.set("session_id", valid_session.session_id)
        yield test_client


@pytest.fixture
def admin_authenticated_client(
    app,
    admin_session: UserSession,
    fresh_session_manager: SessionManager,
) -> Generator[TestClient, None, None]:
    """Create a client with an admin in-memory session cookie."""
    fresh_session_manager.create_session(admin_session)
    with TestClient(app) as test_client:
        test_client.cookies.set("session_id", admin_session.session_id)
        yield test_client


@pytest.fixture
def override_dependencies(app):
    """Provide a helper for explicit FastAPI dependency overrides."""

    def _apply(
        *,
        session: UserSession | None = None,
        calculator: Any | None = None,
        connector: Any | None = None,
        registry: Any | None = None,
    ) -> None:
        if session is not None:
            app.dependency_overrides[deps.get_current_session] = lambda: session
        if calculator is not None:
            app.dependency_overrides[deps.get_indicator_calculator] = lambda: calculator
        if connector is not None:
            app.dependency_overrides[deps.get_dhis2_connector] = lambda: connector
        if registry is not None:
            app.dependency_overrides[deps.get_registry] = lambda: registry

    yield _apply
    app.dependency_overrides.clear()
