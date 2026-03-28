"""
Session-cached wrapper for the live IndicatorCalculator.
"""

from __future__ import annotations

from typing import Any

from app.core.cache import SessionCache, get_session_cache
from app.core.cache_keys import CacheKeys, get_cache_ttl
from app.indicators.calculator import IndicatorCalculator
from app.indicators.models import IndicatorCategory, IndicatorResult, IndicatorResultSet


class CachedIndicatorCalculator:
    """Wrap IndicatorCalculator while preserving the live Prompt 3 API."""

    def __init__(
        self,
        calculator: IndicatorCalculator,
        session_id: str,
        *,
        cache: SessionCache | None = None,
    ) -> None:
        self._calculator = calculator
        self._session_id = session_id
        self._cache = cache or get_session_cache(session_id)

    @property
    def session(self):
        return self._calculator._session

    @property
    def population_data(self):
        return self._calculator._population_data

    def __getattr__(self, item: str) -> Any:
        return getattr(self._calculator, item)

    def _population_value(self, org_unit: str) -> int | None:
        value = self.population_data.get(org_unit)
        return int(value) if value is not None else None

    def set_expected_pregnancies(self, org_unit: str, value: int) -> None:
        self._calculator.set_expected_pregnancies(org_unit, value)
        self.invalidate()

    def clear_expected_pregnancies(self, org_unit: str | None = None) -> None:
        self._calculator.clear_expected_pregnancies(org_unit)
        self.invalidate()

    async def calculate_single(
        self,
        indicator_id: str,
        org_unit: str,
        period: str,
        org_unit_name: str | None = None,
        include_children: bool = False,
        *,
        use_cache: bool = True,
    ) -> IndicatorResult:
        if not use_cache:
            return await self._calculator.calculate_single(
                indicator_id=indicator_id,
                org_unit=org_unit,
                period=period,
                org_unit_name=org_unit_name,
                include_children=include_children,
            )

        key = CacheKeys.indicator_single(
            indicator_id=indicator_id,
            org_unit=org_unit,
            period=period,
            org_unit_name=org_unit_name,
            include_children=include_children,
            population_value=self._population_value(org_unit),
        )
        return await self._cache.get_or_set_async(
            key,
            lambda: self._calculator.calculate_single(
                indicator_id=indicator_id,
                org_unit=org_unit,
                period=period,
                org_unit_name=org_unit_name,
                include_children=include_children,
            ),
            ttl=get_cache_ttl("indicators"),
        )

    async def calculate_all(
        self,
        org_unit: str,
        period: str,
        org_unit_name: str | None = None,
        include_children: bool = False,
        categories: list[IndicatorCategory] | None = None,
        *,
        use_cache: bool = True,
    ) -> IndicatorResultSet:
        if not use_cache:
            return await self._calculator.calculate_all(
                org_unit=org_unit,
                period=period,
                org_unit_name=org_unit_name,
                include_children=include_children,
                categories=categories,
            )

        category_values = [category.value for category in categories] if categories else None
        key = CacheKeys.indicator_batch(
            org_unit=org_unit,
            period=period,
            org_unit_name=org_unit_name,
            include_children=include_children,
            categories=category_values,
            population_value=self._population_value(org_unit),
        )
        result_set = await self._cache.get_or_set_async(
            key,
            lambda: self._calculator.calculate_all(
                org_unit=org_unit,
                period=period,
                org_unit_name=org_unit_name,
                include_children=include_children,
                categories=categories,
            ),
            ttl=get_cache_ttl("indicators"),
        )

        for result in result_set.results:
            single_key = CacheKeys.indicator_single(
                indicator_id=result.indicator_id,
                org_unit=org_unit,
                period=period,
                org_unit_name=org_unit_name,
                include_children=include_children,
                population_value=self._population_value(org_unit),
            )
            self._cache.set(single_key, result, ttl=get_cache_ttl("indicators"))

        return result_set

    def invalidate(self) -> int:
        """Clear all session-scoped indicator caches."""
        deleted = 0
        deleted += self._cache.delete_pattern("indicator:")
        deleted += self._cache.delete_pattern("dhis2:")
        return deleted


def build_cached_calculator(session, population_data: dict[str, int]) -> CachedIndicatorCalculator:
    """Construct the live cached calculator for one authenticated session."""
    return CachedIndicatorCalculator(
        calculator=IndicatorCalculator(session=session, population_data=population_data),
        session_id=session.session_id,
    )
