"""
DHIS2 authentication handler.
Supports Basic Auth and Personal Access Tokens (PAT).
"""

from typing import Optional

import httpx

from app.core.config import get_settings
from app.core.session import AuthMethod, DHIS2Credentials


class DHIS2AuthError(Exception):
    """Raised when DHIS2 authentication fails."""
    pass


class DHIS2AuthHandler:
    """Handles DHIS2 authentication against the target DHIS2 instance."""

    def __init__(self, timeout: Optional[int] = None):
        settings = get_settings()
        self.timeout = timeout or settings.dhis2_timeout_seconds

    async def authenticate_basic(
        self,
        base_url: str,
        username: str,
        password: str,
    ) -> DHIS2Credentials:
        credentials = DHIS2Credentials(
            base_url=base_url.rstrip("/"),
            auth_method=AuthMethod.BASIC,
            username=username,
            password=password,
        )
        await self._verify_and_populate(credentials)
        return credentials

    async def authenticate_pat(
        self,
        base_url: str,
        pat_token: str,
    ) -> DHIS2Credentials:
        credentials = DHIS2Credentials(
            base_url=base_url.rstrip("/"),
            auth_method=AuthMethod.PAT,
            pat_token=pat_token,
        )
        await self._verify_and_populate(credentials)
        return credentials

    async def _verify_and_populate(self, credentials: DHIS2Credentials) -> None:
        """Verify credentials against /api/me and populate user info."""
        url = f"{credentials.base_url}/api/me"
        params = {"fields": "id,name,authorities,organisationUnits[id,name,level]"}

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                response = await client.get(
                    url,
                    headers=credentials.get_auth_header(),
                    params=params,
                )

                if response.status_code == 401:
                    raise DHIS2AuthError("Invalid credentials")
                if response.status_code == 403:
                    raise DHIS2AuthError("Access forbidden - check user permissions")
                if response.status_code != 200:
                    raise DHIS2AuthError(
                        f"Authentication failed: HTTP {response.status_code}"
                    )

                data = response.json()
                credentials.user_id = data.get("id")
                credentials.user_name = data.get("name")
                credentials.authorities = self._normalize_authorities(data.get("authorities", []))
                credentials.org_units = data.get("organisationUnits", [])

                if not credentials.org_units:
                    raise DHIS2AuthError("User has no assigned organisation units")

            except httpx.TimeoutException as exc:
                raise DHIS2AuthError(
                    f"Connection timeout to {credentials.base_url}"
                ) from exc
            except httpx.ConnectError as exc:
                raise DHIS2AuthError(
                    f"Cannot connect to {credentials.base_url}"
                ) from exc

    @staticmethod
    def _normalize_authorities(authorities: list[object]) -> list[str]:
        """Normalize DHIS2 authority payloads into plain strings."""
        normalized: list[str] = []
        for authority in authorities or []:
            if isinstance(authority, str) and authority:
                normalized.append(authority)
                continue
            if isinstance(authority, dict):
                value = authority.get("authority") or authority.get("name")
                if value:
                    normalized.append(str(value))
        return normalized

    async def logout(self, credentials: DHIS2Credentials) -> None:
        """Clear sensitive credential data."""
        credentials.clear_secrets()
