"""
Supply chain service -- orchestrates data fetch, enrichment, and caching.

Uses the existing CachedDHIS2Connector for data, the indicator registry for
UID resolution, and the session cache for derived report objects.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.connectors.cached_connector import build_cached_connector
from app.connectors.dhis2_connector import DHIS2Connector
from app.core.cache import SessionCache, get_session_cache
from app.core.cache_keys import get_cache_ttl, make_key
from app.core.session import UserSession
from app.indicators.cached_calculator import CachedIndicatorCalculator
from app.indicators.models import IndicatorCategory
from app.indicators.registry import get_indicator_registry
from app.supply.alerts import generate_commodity_alerts
from app.supply.commodities import get_mapped_commodities, get_unmapped_commodities
from app.supply.forecasting import compute_forecast, compute_metrics
from app.supply.models import (
    Commodity,
    EnrichedCommodity,
    StockSnapshot,
    SupplyReport,
)
from app.supply.validation import validate_snapshot

logger = logging.getLogger(__name__)


class SupplyService:
    """
    Stateless supply-chain service.

    One instance per request.  Caches the full SupplyReport per session.
    SessionCache.get / .set are synchronous -- do NOT await them.
    """

    def __init__(
        self,
        session: UserSession,
        calculator: CachedIndicatorCalculator,
        session_cache: Optional[SessionCache] = None,
    ):
        self._session = session
        self._calculator = calculator
        self._cache = session_cache or get_session_cache(session.session_id)
        self._registry = get_indicator_registry()

    async def get_supply_report(
        self,
        org_unit: str,
        period: str,
        org_unit_name: Optional[str] = None,
        *,
        use_cache: bool = True,
    ) -> SupplyReport:
        """Build (or return cached) enriched supply report."""
        cache_key = make_key("supply", "report", {
            "org_unit": org_unit,
            "period": period,
        })

        if use_cache:
            cached = self._cache.get(cache_key)
            if cached is not None:
                return cached

        report = await self._build_report(org_unit, period, org_unit_name)

        if use_cache:
            ttl = get_cache_ttl("aggregate")
            self._cache.set(cache_key, report, ttl=ttl)
        return report

    async def _build_report(
        self,
        org_unit: str,
        period: str,
        org_unit_name: Optional[str],
    ) -> SupplyReport:
        """Fetch data and build the enriched supply report."""
        generated_at = datetime.now(timezone.utc)
        mapped = get_mapped_commodities()
        unmapped = get_unmapped_commodities()

        # Fetch indicator results for supply category
        result_set = await self._calculator.calculate_all(
            org_unit=org_unit,
            period=period,
            org_unit_name=org_unit_name,
            categories=[IndicatorCategory.SUPPLY],
        )
        result_map = {r.indicator_id: r for r in result_set.results}

        # Fetch all raw DHIS2 values for mapped commodities
        raw_values = await self._fetch_raw_values(mapped, org_unit, period)

        # Build enriched commodity rows
        enriched: list[EnrichedCommodity] = []
        total_alerts_critical = 0
        total_alerts_warning = 0

        for commodity in mapped:
            snapshot = self._build_snapshot(commodity, result_map, raw_values, period)
            metrics = compute_metrics(snapshot)
            forecast = compute_forecast(
                commodity_id=commodity.id,
                snapshot=snapshot,
                metrics=metrics,
                reorder_months=commodity.reorder_level_months,
                max_stock_months=commodity.max_stock_months,
            )
            findings = validate_snapshot(commodity, snapshot)

            ec = EnrichedCommodity(
                commodity=commodity,
                snapshot=snapshot,
                metrics=metrics,
                forecast=forecast,
                validation=findings,
            )
            ec.alerts = generate_commodity_alerts(ec)

            for a in ec.alerts:
                if a.severity.value == "critical":
                    total_alerts_critical += 1
                elif a.severity.value == "warning":
                    total_alerts_warning += 1

            enriched.append(ec)

        summary = {
            "total_mapped": len(mapped),
            "total_unmapped": len(unmapped),
            "critical_alerts": total_alerts_critical,
            "warning_alerts": total_alerts_warning,
        }

        return SupplyReport(
            org_unit=org_unit,
            org_unit_name=org_unit_name,
            period=period,
            generated_at=generated_at,
            commodities=enriched,
            unmapped_commodities=unmapped,
            summary=summary,
        )

    async def _fetch_raw_values(
        self,
        commodities: list[Commodity],
        org_unit: str,
        period: str,
    ) -> dict[str, Optional[float]]:
        """Fetch raw DHIS2 values for all mapping codes on mapped commodities."""
        codes_needed: set[str] = set()
        for c in commodities:
            m = c.mapping
            for code in [m.consumed, m.stockout_days, m.stock_on_hand, m.expired]:
                if code:
                    codes_needed.add(code)

        if not codes_needed:
            return {}

        uids: list[str] = []
        code_to_uid: dict[str, str] = {}
        for code in codes_needed:
            uid = self._registry.get_data_element_uid(code)
            if uid:
                uids.append(uid)
                code_to_uid[code] = uid

        if not uids:
            return {}

        async with build_cached_connector(self._session) as connector:
            values = await connector.get_data_values(
                data_elements=uids,
                org_unit=org_unit,
                period=period,
            )

        result: dict[str, Optional[float]] = {}
        for code, uid in code_to_uid.items():
            result[code] = values.get(uid)
        return result

    def _build_snapshot(
        self,
        commodity: Commodity,
        result_map: dict[str, Any],
        raw_values: dict[str, Optional[float]],
        period: str,
    ) -> StockSnapshot:
        """Assemble a StockSnapshot from indicator results with raw-value fallback."""
        m = commodity.mapping
        period_days = DHIS2Connector.get_period_days(period)

        # Start with raw values as baseline
        consumed = raw_values.get(m.consumed) if m.consumed else None
        stockout_days = raw_values.get(m.stockout_days) if m.stockout_days else None
        stock_on_hand = raw_values.get(m.stock_on_hand) if m.stock_on_hand else None
        expired = raw_values.get(m.expired) if m.expired else None
        days_of_use = None

        # Prefer indicator results over raw values where available
        if m.consumed_indicator:
            ind = result_map.get(m.consumed_indicator)
            if ind and ind.result_value is not None:
                consumed = ind.result_value

        if m.stockout_days_indicator:
            ind = result_map.get(m.stockout_days_indicator)
            if ind and ind.result_value is not None:
                stockout_days = ind.result_value

        if m.days_of_use_indicator:
            dou_result = result_map.get(m.days_of_use_indicator)
            if dou_result:
                if dou_result.result_value is not None:
                    days_of_use = dou_result.result_value
                # DOU numerator is SOH in the calculator; use as fallback
                if dou_result.numerator_value is not None and stock_on_hand is None:
                    stock_on_hand = dou_result.numerator_value

        return StockSnapshot(
            consumed=consumed,
            stockout_days=stockout_days,
            stock_on_hand=stock_on_hand,
            expired=expired,
            days_of_use=days_of_use,
            period_days=period_days,
        )
