"""
DHIS2 API connector for data extraction.
All methods require an authenticated session.
"""

import asyncio
import calendar
import logging
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import httpx

from app.connectors.schemas import (
    AnalyticsResponse,
    CategoryOptionComboMeta,
    CompletionStatus,
    DataElementMeta,
    DataValueSet,
    OrgUnit,
)
from app.core.config import get_settings, load_yaml_config
from app.core.connection_pool import get_async_client
from app.core.session import UserSession

logger = logging.getLogger(__name__)


class DHIS2Error(Exception):
    """Base exception for DHIS2 API errors."""


class DHIS2NotAuthenticated(DHIS2Error):
    """Raised when session is not authenticated."""


class DHIS2APIError(DHIS2Error):
    """Raised when DHIS2 API returns an error."""

    def __init__(self, message: str, status_code: int = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


class PeriodType(Enum):
    MONTHLY = "monthly"
    WEEKLY = "weekly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class DHIS2Connector:
    """
    DHIS2 API connector.

    All methods are stateless - credentials come from session.
    No data is cached or persisted.
    Use as async context manager for efficient connection reuse.
    """

    def __init__(self, session: UserSession):
        if not session.is_authenticated:
            raise DHIS2NotAuthenticated("Session is not authenticated")

        self._credentials = session.credentials
        self._settings = get_settings()
        self._base_url = self._credentials.base_url.rstrip("/")
        self._mappings = load_yaml_config("mappings.yaml")
        self._client: Optional[httpx.AsyncClient] = None

    async def __aenter__(self):
        self._client = self._create_client()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self) -> None:
        """Release the local client reference.

        The underlying httpx client is pooled application-wide and is closed
        during app shutdown, not per connector instance.
        """
        self._client = None

    def _get_headers(self) -> Dict[str, str]:
        headers = self._credentials.get_auth_header()
        headers["Accept"] = "application/json"
        return headers

    def _create_client(self) -> httpx.AsyncClient:
        """Return the shared pooled HTTP client."""
        return get_async_client()

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = self._create_client()
        return self._client

    def _get_configured_data_element_uid(self, code: str) -> str:
        """Return a data element UID from config/mappings.yaml."""
        data_elements = self._mappings.get("data_elements", {})
        mapping = data_elements.get(code, {})
        uid = mapping.get("uid")
        if not uid:
            raise DHIS2Error(f"Missing data element UID mapping for {code}")
        return uid

    def _get_an21_positive_cocs(self) -> tuple[str, List[str]]:
        """Return configured AN21 positive-result parent UID and COC list."""
        coc_section = self._mappings.get("category_option_combos", {})
        an21_pos = coc_section.get("AN21_POS", {})
        parent_uid = an21_pos.get("parent_uid")
        combos = an21_pos.get("combos", [])
        coc_uids = [combo.get("coc_uid") for combo in combos if combo.get("coc_uid")]

        if not parent_uid:
            raise DHIS2Error("Missing AN21_POS parent UID in mappings.yaml")
        if not coc_uids:
            raise DHIS2Error("Missing AN21_POS category option combos in mappings.yaml")

        return parent_uid, coc_uids

    async def _request_with_retry(
        self,
        method: str,
        endpoint: str,
        params: Dict = None,
        data: Dict = None,
        timeout: int = None,
    ) -> Dict[str, Any]:
        """
        Make authenticated request to DHIS2 API with retry logic.
        Retries on timeout, connection error, 429, and 5xx.
        """
        url = f"{self._base_url}/api/{endpoint.lstrip('/')}"
        timeout = timeout or self._settings.dhis2_timeout_seconds
        max_retries = self._settings.dhis2_max_retries
        client = await self._get_client()

        last_error = None

        for attempt in range(max_retries + 1):
            try:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=self._get_headers(),
                    params=params,
                    json=data,
                    timeout=timeout,
                )

                if response.status_code == 401:
                    raise DHIS2NotAuthenticated("Session expired or invalid")

                if response.status_code == 429:
                    retry_after = int(response.headers.get("Retry-After", 5))
                    if attempt < max_retries:
                        logger.warning(
                            "Rate limited, waiting %ss before retry", retry_after
                        )
                        await asyncio.sleep(retry_after)
                        continue

                if response.status_code >= 500:
                    if attempt < max_retries:
                        wait_time = 2 ** attempt
                        logger.warning(
                            "Server error %s, retry in %ss",
                            response.status_code,
                            wait_time,
                        )
                        await asyncio.sleep(wait_time)
                        continue
                    raise DHIS2APIError(
                        f"Server error: HTTP {response.status_code}",
                        status_code=response.status_code,
                        retryable=True,
                    )

                if response.status_code >= 400:
                    error_msg = response.text[:500]
                    raise DHIS2APIError(
                        f"DHIS2 API error: {error_msg}",
                        status_code=response.status_code,
                        retryable=False,
                    )

                return response.json() if response.text else {}

            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    logger.warning(
                        "Timeout, retry %s/%s in %ss",
                        attempt + 1,
                        max_retries,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                raise DHIS2APIError(
                    "Request timeout after retries", status_code=408, retryable=True
                ) from exc

            except httpx.ConnectError as exc:
                last_error = exc
                if attempt < max_retries:
                    wait_time = 2 ** attempt
                    logger.warning(
                        "Connection failed, retry %s/%s in %ss",
                        attempt + 1,
                        max_retries,
                        wait_time,
                    )
                    await asyncio.sleep(wait_time)
                    continue
                raise DHIS2APIError(
                    "Connection failed after retries", status_code=503, retryable=True
                ) from exc

        raise DHIS2APIError(f"Request failed: {last_error}", retryable=True)

    # =========================================================================
    # DATA VALUE METHODS
    # =========================================================================
    #
    # CRITICAL API NOTE:
    # - Use /api/analytics as PRIMARY extraction method for data element queries.
    # - Use /api/dataValueSets only when dataset UID is known.
    # - smoke_test_connection() MUST be run on first connect.
    # =========================================================================

    async def get_data_values(
        self,
        data_elements: List[str],
        org_unit: str,
        period: str,
        include_children: bool = False,
    ) -> Dict[str, Optional[float]]:
        """
        Fetch data values for specified elements using analytics API.

        If include_children=True, child rows may be returned separately by DHIS2.
        In that case, values are SUMMED across child org units per data element.
        This is appropriate for additive count indicators only.
        For detailed child-level analysis, use get_analytics() instead.

        Returns:
            Dict mapping data element UID to numeric value (or None)
        """
        ou_param = org_unit
        if include_children:
            ou_param = f"{org_unit};CHILDREN"

        params = {
            "dimension": [
                f"dx:{';'.join(data_elements)}",
                f"pe:{period}",
                f"ou:{ou_param}",
            ],
            "skipMeta": "true",
        }

        response = await self._request_with_retry("GET", "analytics", params=params)

        results: Dict[str, Optional[float]] = {de: None for de in data_elements}
        headers = response.get("headers", [])
        rows = response.get("rows", [])

        dx_idx = next((i for i, h in enumerate(headers) if h.get("name") == "dx"), 0)
        val_idx = next(
            (i for i, h in enumerate(headers) if h.get("name") == "value"), -1
        )

        if val_idx == -1 and headers:
            val_idx = len(headers) - 1

        for row in rows:
            if len(row) <= max(dx_idx, val_idx):
                continue

            de_uid = row[dx_idx]
            try:
                row_value = float(row[val_idx])
            except (ValueError, TypeError, IndexError):
                continue

            if results.get(de_uid) is None:
                results[de_uid] = row_value
            else:
                # Sum additive values when multiple child rows exist
                results[de_uid] += row_value

        return results

    async def get_data_value(
        self,
        data_element: str,
        org_unit: str,
        period: str,
        include_children: bool = False,
    ) -> Optional[float]:
        results = await self.get_data_values(
            data_elements=[data_element],
            org_unit=org_unit,
            period=period,
            include_children=include_children,
        )
        return results.get(data_element)

    async def get_data_values_by_dataset(
        self,
        dataset_uid: str,
        org_unit: str,
        period: str,
        include_children: bool = False,
    ) -> DataValueSet:
        """
        Fetch all data values for a dataset using dataValueSets API.
        Correct usage requires dataset UID.
        """
        params = {
            "dataSet": dataset_uid,
            "orgUnit": org_unit,
            "period": period,
            "children": str(include_children).lower(),
        }

        response = await self._request_with_retry("GET", "dataValueSets", params=params)
        return DataValueSet.from_dhis2_response(response)

    async def get_disaggregated_values(
        self,
        data_element: str,
        category_option_combos: List[str],
        org_unit: str,
        period: str,
    ) -> Dict[str, Optional[float]]:
        """
        Fetch values for a data element across multiple COCs using analytics.

        Uses dx in DE.COC form:
            dx:dataElement.coc1;dataElement.coc2;...

        Returns:
            Dict mapping COC UID to value (or None)
        """
        results: Dict[str, Optional[float]] = {coc: None for coc in category_option_combos}
        dx_items = [f"{data_element}.{coc}" for coc in category_option_combos]

        params = {
            "dimension": [
                f"dx:{';'.join(dx_items)}",
                f"pe:{period}",
                f"ou:{org_unit}",
            ],
            "skipMeta": "true",
        }

        response = await self._request_with_retry("GET", "analytics", params=params)
        headers = response.get("headers", [])
        rows = response.get("rows", [])

        dx_idx = next((i for i, h in enumerate(headers) if h.get("name") == "dx"), 0)
        val_idx = next(
            (i for i, h in enumerate(headers) if h.get("name") == "value"), -1
        )

        if val_idx == -1 and headers:
            val_idx = len(headers) - 1

        for row in rows:
            if len(row) <= max(dx_idx, val_idx):
                continue

            dx_value = row[dx_idx]  # expected format: DE.COC
            if "." not in dx_value:
                continue

            coc = dx_value.split(".")[-1]
            if coc not in results:
                continue

            try:
                results[coc] = float(row[val_idx])
            except (ValueError, TypeError):
                continue

        return results

    async def get_an21_pos_total(
        self,
        org_unit: str,
        period: str,
    ) -> float:
        """
        Get AN21-POS total by summing configured positive-result COCs.
        Convenience method for later indicator logic.
        """
        an21_uid, pos_cocs = self._get_an21_positive_cocs()

        values = await self.get_disaggregated_values(
            data_element=an21_uid,
            category_option_combos=pos_cocs,
            org_unit=org_unit,
            period=period,
        )

        return sum(v for v in values.values() if v is not None)

    # =========================================================================
    # ANALYTICS METHODS
    # =========================================================================

    async def get_analytics(
        self,
        data_elements: List[str],
        org_units: List[str],
        periods: List[str],
        include_children: bool = False,
    ) -> AnalyticsResponse:
        """
        Fetch aggregated analytics data.

        Use this for multi-period/multi-org extraction when row-level detail matters.
        """
        ou_param = ";".join(org_units)
        if include_children:
            ou_param += ";CHILDREN"

        params = {
            "dimension": [
                f"dx:{';'.join(data_elements)}",
                f"pe:{';'.join(periods)}",
                f"ou:{ou_param}",
            ],
            "skipMeta": "false",
            "hierarchyMeta": "true",
        }

        response = await self._request_with_retry("GET", "analytics", params=params)
        return AnalyticsResponse.from_dhis2_response(response)

    # =========================================================================
    # COMPLETENESS METHODS
    # =========================================================================

    async def get_reporting_completeness(
        self,
        dataset_uid: str,
        org_unit: str,
        period: str,
        include_children: bool = False,
    ) -> CompletionStatus:
        """
        Check reporting completeness for a dataset.

        # TODO: Configure actual dataset UID(s) for HMIS 105 / HMIS 033b completeness.
        """
        params = {
            "dataSet": dataset_uid,
            "period": period,
            "orgUnit": org_unit,
            "children": str(include_children).lower(),
        }

        response = await self._request_with_retry(
            "GET",
            "completeDataSetRegistrations",
            params=params,
        )

        expected_count = 1
        if include_children:
            expected_count = await self._get_expected_reporting_units(
                org_unit, dataset_uid
            )

        return CompletionStatus.from_dhis2_response(
            response,
            org_unit,
            period,
            expected_count=expected_count,
        )

    async def _get_expected_reporting_units(
        self,
        parent_org_unit: str,
        dataset_uid: str,
    ) -> int:
        """
        Get count of org units expected to report for a dataset.

        Note:
        This is a best-effort implementation and should be validated against
        the target Uganda HMIS dataset assignment structure.
        """
        params = {
            "fields": "id",
            "filter": [
                f"path:like:{parent_org_unit}",
                f"dataSets.id:eq:{dataset_uid}",
            ],
            "paging": "false",
        }

        try:
            response = await self._request_with_retry(
                "GET",
                "organisationUnits",
                params=params,
            )
            return len(response.get("organisationUnits", []))
        except DHIS2APIError:
            logger.warning(
                "Could not determine expected reporting units for dataset %s",
                dataset_uid,
            )
            return 1

    # =========================================================================
    # METADATA VALIDATION METHODS
    # =========================================================================

    async def get_data_element(self, uid: str) -> DataElementMeta:
        params = {
            "fields": "id,name,shortName,valueType,aggregationType,categoryCombo[id,name]"
        }
        response = await self._request_with_retry(
            "GET",
            f"dataElements/{uid}",
            params=params,
        )
        return DataElementMeta.from_dhis2_response(response)

    async def get_category_option_combo(self, uid: str) -> CategoryOptionComboMeta:
        params = {
            "fields": "id,name,categoryCombo[id,name],categoryOptions[id,name]"
        }
        response = await self._request_with_retry(
            "GET",
            f"categoryOptionCombos/{uid}",
            params=params,
        )
        return CategoryOptionComboMeta.from_dhis2_response(response)

    async def validate_uids(self, uids: List[str]) -> Dict[str, Union[bool, str]]:
        """
        Validate that UIDs exist in DHIS2.

        Returns:
        - True if found
        - False if confirmed not found (404)
        - "error" if transient/unknown error
        """
        results: Dict[str, Union[bool, str]] = {}

        for uid in uids:
            try:
                await self._request_with_retry(
                    "GET",
                    f"dataElements/{uid}",
                    params={"fields": "id"},
                )
                results[uid] = True
                continue
            except DHIS2APIError as e:
                if e.status_code != 404:
                    logger.warning(
                        "Non-404 error validating data element %s: %s", uid, e
                    )
                    results[uid] = "error"
                    continue

            try:
                await self._request_with_retry(
                    "GET",
                    f"categoryOptionCombos/{uid}",
                    params={"fields": "id"},
                )
                results[uid] = True
            except DHIS2APIError as e:
                if e.status_code == 404:
                    results[uid] = False
                else:
                    logger.warning("Non-404 error validating COC %s: %s", uid, e)
                    results[uid] = "error"

        return results

    async def smoke_test_connection(self) -> Dict[str, Any]:
        """
        Smoke test the DHIS2 connection and API behavior.

        MUST be run on first connect to verify:
        1. Authentication works
        2. Analytics API responds
        3. A known data element can be queried
        """
        results: Dict[str, Any] = {
            "success": False,
            "user": self._credentials.user_name,
            "org_units": len(self._credentials.org_units),
            "analytics_works": False,
            "sample_uid_valid": False,
            "errors": [],
        }

        if not self._credentials.org_units:
            results["errors"].append("No org units assigned to user")
            return results

        test_org_unit = self._credentials.org_units[0].get("id")

        try:
            test_de = self._get_configured_data_element_uid("AN01a")
            params = {
                "dimension": [
                    f"dx:{test_de}",
                    "pe:LAST_12_MONTHS",
                    f"ou:{test_org_unit}",
                ],
                "skipMeta": "true",
            }
            response = await self._request_with_retry("GET", "analytics", params=params)
            results["analytics_works"] = True

            if "rows" in response or "headers" in response:
                results["sample_uid_valid"] = True

        except DHIS2APIError as e:
            results["errors"].append(f"Analytics API failed: {e}")

        try:
            await self.get_data_element(self._get_configured_data_element_uid("AN01a"))
            results["sample_uid_valid"] = True
        except DHIS2Error as e:
            results["errors"].append(str(e))
        except DHIS2APIError as e:
            if e.status_code == 404:
                results["errors"].append(
                    "Data element AN01a not found - verify UIDs match target instance"
                )
            else:
                results["errors"].append(f"Metadata API error: {e}")

        results["success"] = (
            results["analytics_works"]
            and results["sample_uid_valid"]
            and len(results["errors"]) == 0
        )

        return results

    # =========================================================================
    # ORG UNIT METHODS
    # =========================================================================

    async def get_org_unit(self, uid: str) -> OrgUnit:
        params = {"fields": "id,name,level,parent[id,name],children[id,name]"}
        response = await self._request_with_retry(
            "GET",
            f"organisationUnits/{uid}",
            params=params,
        )
        return OrgUnit.from_dhis2_response(response)

    async def get_org_unit_hierarchy(
        self,
        root_uid: str,
        max_level: int = None,
    ) -> List[OrgUnit]:
        filters = [f"path:like:{root_uid}"]
        if max_level:
            filters.append(f"level:le:{max_level}")

        params = {
            "fields": "id,name,level,parent[id,name]",
            "paging": "false",
            "filter": filters,
        }

        response = await self._request_with_retry(
            "GET",
            "organisationUnits",
            params=params,
        )

        return [
            OrgUnit.from_dhis2_response(ou_data)
            for ou_data in response.get("organisationUnits", [])
        ]

    async def get_user_org_units(self) -> List[OrgUnit]:
        return [
            OrgUnit(uid=ou.get("id"), name=ou.get("name"), level=ou.get("level"))
            for ou in self._credentials.org_units
        ]

    # =========================================================================
    # UTILITY METHODS
    # =========================================================================

    @staticmethod
    def format_period(
        year: int,
        month: int = None,
        week: int = None,
        period_type: PeriodType = PeriodType.MONTHLY,
    ) -> str:
        if period_type == PeriodType.WEEKLY:
            if week is None:
                raise ValueError("Week required for weekly period")
            return f"{year}W{week:02d}"

        if period_type == PeriodType.MONTHLY:
            if month is None:
                raise ValueError("Month required for monthly period")
            return f"{year}{month:02d}"

        if period_type == PeriodType.QUARTERLY:
            if month is None:
                raise ValueError("Month required to determine quarter")
            quarter = (month - 1) // 3 + 1
            return f"{year}Q{quarter}"

        if period_type == PeriodType.YEARLY:
            return str(year)

        raise ValueError(f"Unknown period type: {period_type}")

    @staticmethod
    def get_period_days(period: str) -> int:
        """
        Get number of days in a DHIS2 period.
        Used later for DOU calculations.
        """
        if "W" in period:
            return 7
        if "Q" in period:
            # Approximation acceptable for now; refine later if needed
            return 90
        if len(period) == 6:  # Monthly: YYYYMM
            year = int(period[:4])
            month = int(period[4:6])
            return calendar.monthrange(year, month)[1]
        if len(period) == 4:  # Yearly: YYYY
            year = int(period)
            return 366 if calendar.isleap(year) else 365

        logger.warning("Unknown period format %s, defaulting to 30 days", period)
        return 30
