"""Integration fixtures using the live app plus a mock DHIS2 server."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from app.auth.roles import resolve_user_role, store_role_in_session
from app.core.session import AuthMethod, DHIS2Credentials, UserSession
from .mock_dhis2 import MockDHIS2Server


@pytest.fixture
def mock_dhis2_server() -> Generator[MockDHIS2Server, None, None]:
    """Start and stop the mock DHIS2 server."""
    server = MockDHIS2Server()
    server.start()
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def integration_session(mock_dhis2_server: MockDHIS2Server) -> UserSession:
    """Create a real in-memory session pointed at the mock DHIS2 server."""
    credentials = DHIS2Credentials(
        base_url=mock_dhis2_server.base_url,
        auth_method=AuthMethod.PAT,
        pat_token="integration-pat",
        user_id="integration-user",
        user_name="Integration User",
        authorities=["F_EXPORT_DATA", "F_PERFORM_ANALYTICS", "F_DATAVALUE_ADD"],
        org_units=[
            {"id": "OU_FACILITY_1", "name": "Mock Facility", "level": 5},
            {"id": "OU_DISTRICT_1", "name": "Mock District", "level": 3},
        ],
    )
    session = UserSession(
        session_id="integration_session_123",
        created_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
        credentials=credentials,
    )
    store_role_in_session(
        session,
        resolve_user_role(
            user_id=credentials.user_id or "integration-user",
            username=credentials.user_name or "Integration User",
            authorities=credentials.authorities,
            org_units=credentials.org_units,
        ),
    )
    session.user_data["csrf_token"] = "integration-csrf-token"
    return session


@pytest.fixture
def integration_client(
    app,
    integration_session: UserSession,
    fresh_session_manager,
) -> Generator[TestClient, None, None]:
    """Create a client backed by the real app and mock-DHIS2 session."""
    fresh_session_manager.create_session(integration_session)
    with TestClient(app) as test_client:
        test_client.cookies.set("session_id", integration_session.session_id)
        yield test_client
