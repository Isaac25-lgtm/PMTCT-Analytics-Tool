"""
Unit tests for Prompt 16 supply chain module.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.supply.commodities import (
    get_mapped_commodities,
    get_thresholds,
    get_unmapped_commodities,
    load_commodities,
    reset_cache,
)
from app.supply.forecasting import compute_forecast, compute_metrics
from app.supply.models import (
    AlertSeverity,
    Commodity,
    CommodityMapping,
    EnrichedCommodity,
    ForecastResult,
    MappingStatus,
    StockMetrics,
    StockSnapshot,
    StockStatus,
    SupplyReport,
    ValidationSeverity,
)
from app.supply.validation import validate_all, validate_snapshot
from app.supply.alerts import generate_commodity_alerts


@pytest.fixture(autouse=True)
def _reset_commodity_cache():
    reset_cache()
    yield
    reset_cache()


class TestCommodityRegistry:
    def test_load_all_commodities(self):
        commodities = load_commodities()
        assert len(commodities) >= 6
        ids = {c.id for c in commodities}
        assert "hbsag_kits" in ids
        assert "duo_kits" in ids
        assert "benzathine_penicillin" in ids

    def test_mapped_commodities_have_codes(self):
        mapped = get_mapped_commodities()
        assert len(mapped) == 2
        for c in mapped:
            assert c.mapping_status == MappingStatus.MAPPED
            assert c.mapping.consumed is not None
            assert c.mapping.stock_on_hand is not None

    def test_mapped_commodities_have_indicator_ids(self):
        """Config-driven indicator IDs instead of hardcoded dicts."""
        mapped = get_mapped_commodities()
        for c in mapped:
            assert c.mapping.consumed_indicator is not None
            assert c.mapping.stockout_days_indicator is not None
            assert c.mapping.days_of_use_indicator is not None

    def test_unmapped_commodities_are_pending(self):
        unmapped = get_unmapped_commodities()
        assert len(unmapped) >= 4
        for c in unmapped:
            assert c.mapping_status == MappingStatus.MAPPING_PENDING

    def test_thresholds_have_defaults(self):
        thresholds = get_thresholds()
        assert thresholds["stockout_dou"] == 0
        assert thresholds["imminent_stockout_dou"] == 7
        assert thresholds["low_stock_dou"] == 30
        assert thresholds["overstock_dou"] == 180


class TestForecasting:
    def test_compute_metrics_basic(self):
        snapshot = StockSnapshot(
            consumed=90,
            stockout_days=0,
            stock_on_hand=300,
            period_days=30,
        )
        metrics = compute_metrics(snapshot)
        assert metrics.average_daily_consumption == 3.0
        assert metrics.adjusted_adc == 3.0
        assert metrics.days_of_use == 100.0
        assert metrics.months_of_stock == 3.3
        assert metrics.status == StockStatus.OK

    def test_stockout_correction(self):
        snapshot = StockSnapshot(
            consumed=60,
            stockout_days=10,
            stock_on_hand=30,
            period_days=30,
        )
        metrics = compute_metrics(snapshot)
        assert metrics.adjusted_adc == 3.0
        assert metrics.days_of_use == 10.0
        # DOU=10 is >= 7 and < 30, so status is LOW
        assert metrics.status == StockStatus.LOW

    def test_zero_consumption_with_stock(self):
        snapshot = StockSnapshot(
            consumed=0,
            stockout_days=0,
            stock_on_hand=100,
            period_days=30,
        )
        metrics = compute_metrics(snapshot)
        assert metrics.days_of_use is None
        assert metrics.status == StockStatus.UNKNOWN

    def test_stockout_status(self):
        snapshot = StockSnapshot(
            consumed=90,
            stockout_days=0,
            stock_on_hand=0,
            period_days=30,
        )
        metrics = compute_metrics(snapshot)
        assert metrics.days_of_use == 0.0
        assert metrics.status == StockStatus.STOCKOUT

    def test_missing_data(self):
        snapshot = StockSnapshot()
        metrics = compute_metrics(snapshot)
        assert metrics.status == StockStatus.UNKNOWN

    def test_compute_forecast_with_reorder(self):
        snapshot = StockSnapshot(
            consumed=90,
            stockout_days=0,
            stock_on_hand=50,
            period_days=30,
        )
        metrics = compute_metrics(snapshot)
        forecast = compute_forecast(
            commodity_id="test",
            snapshot=snapshot,
            metrics=metrics,
            reorder_months=2.0,
            max_stock_months=6.0,
        )
        assert forecast.reorder_needed is True
        assert forecast.reorder_quantity is not None
        assert forecast.reorder_quantity > 0
        assert 30 in forecast.horizons
        assert 60 in forecast.horizons
        assert 90 in forecast.horizons

    def test_compute_forecast_adequate_stock(self):
        snapshot = StockSnapshot(
            consumed=30,
            stockout_days=0,
            stock_on_hand=500,
            period_days=30,
        )
        metrics = compute_metrics(snapshot)
        forecast = compute_forecast(
            commodity_id="test",
            snapshot=snapshot,
            metrics=metrics,
        )
        assert forecast.reorder_needed is False
        assert forecast.confidence == "normal"


class TestValidation:
    def _make_commodity(self) -> Commodity:
        return Commodity(
            id="test",
            name="Test",
            unit="kits",
            mapping_status=MappingStatus.MAPPED,
        )

    def test_negative_stock_on_hand(self):
        findings = validate_snapshot(
            self._make_commodity(),
            StockSnapshot(stock_on_hand=-5, period_days=30),
        )
        assert any(f.severity == ValidationSeverity.ERROR and "negative" in f.message.lower() for f in findings)

    def test_stockout_days_exceeds_period(self):
        findings = validate_snapshot(
            self._make_commodity(),
            StockSnapshot(stockout_days=35, period_days=30),
        )
        assert any(f.severity == ValidationSeverity.ERROR and "exceeds" in f.message.lower() for f in findings)

    def test_high_stockout_with_positive_soh(self):
        findings = validate_snapshot(
            self._make_commodity(),
            StockSnapshot(stockout_days=20, stock_on_hand=100, period_days=30),
        )
        assert any(f.severity == ValidationSeverity.WARNING for f in findings)

    def test_expired_exceeds_consumed_plus_soh(self):
        findings = validate_snapshot(
            self._make_commodity(),
            StockSnapshot(consumed=10, stock_on_hand=5, expired=20, period_days=30),
        )
        assert any("expired" in f.message.lower() for f in findings)

    def test_valid_snapshot_no_findings(self):
        findings = validate_snapshot(
            self._make_commodity(),
            StockSnapshot(consumed=90, stockout_days=0, stock_on_hand=300, period_days=30),
        )
        assert len(findings) == 0

    def test_validate_all(self):
        c = self._make_commodity()
        result = validate_all([
            (c, StockSnapshot(stock_on_hand=-1, period_days=30)),
            (c, StockSnapshot(stockout_days=31, period_days=30)),
        ])
        assert result.error_count >= 2


class TestSupplyAlerts:
    def _make_ec(
        self,
        soh: float | None = 100,
        dou: float | None = 60,
        stockout_days: float | None = 0,
        expired: float | None = None,
        reorder_needed: bool = False,
        reorder_qty: float | None = None,
    ) -> EnrichedCommodity:
        return EnrichedCommodity(
            commodity=Commodity(
                id="test",
                name="Test Kit",
                unit="kits",
                mapping_status=MappingStatus.MAPPED,
            ),
            snapshot=StockSnapshot(
                stock_on_hand=soh,
                stockout_days=stockout_days,
                expired=expired,
                period_days=30,
            ),
            metrics=StockMetrics(days_of_use=dou, status=StockStatus.OK),
            forecast=ForecastResult(
                commodity_id="test",
                reorder_needed=reorder_needed,
                reorder_quantity=reorder_qty,
            ),
        )

    def test_stockout_alert(self):
        ec = self._make_ec(soh=0, dou=0)
        alerts = generate_commodity_alerts(ec)
        assert any(a.alert_type == "stockout" for a in alerts)

    def test_imminent_stockout_alert(self):
        ec = self._make_ec(soh=10, dou=5)
        alerts = generate_commodity_alerts(ec)
        assert any(a.alert_type == "imminent_stockout" for a in alerts)

    def test_low_stock_alert(self):
        ec = self._make_ec(soh=50, dou=20)
        alerts = generate_commodity_alerts(ec)
        assert any(a.alert_type == "low_stock" for a in alerts)

    def test_overstock_alert(self):
        ec = self._make_ec(soh=1000, dou=200)
        alerts = generate_commodity_alerts(ec)
        assert any(a.alert_type == "overstock" for a in alerts)

    def test_stockout_days_reported(self):
        ec = self._make_ec(stockout_days=5)
        alerts = generate_commodity_alerts(ec)
        assert any(a.alert_type == "stockout_days_reported" for a in alerts)

    def test_expiry_signal(self):
        ec = self._make_ec(expired=10)
        alerts = generate_commodity_alerts(ec)
        assert any(a.alert_type == "expiry_wastage" for a in alerts)

    def test_reorder_alert(self):
        ec = self._make_ec(reorder_needed=True, reorder_qty=500)
        alerts = generate_commodity_alerts(ec)
        assert any(a.alert_type == "reorder_needed" for a in alerts)

    def test_adequate_stock_no_critical(self):
        ec = self._make_ec(soh=100, dou=60, stockout_days=0)
        alerts = generate_commodity_alerts(ec)
        assert not any(a.severity == AlertSeverity.CRITICAL for a in alerts)


class TestSupplyReport:
    def test_generated_at_is_datetime(self):
        """Fix 5: generated_at should be datetime, not str."""
        report = SupplyReport(
            org_unit="ou123",
            org_unit_name="Test",
            period="202401",
            generated_at=datetime.now(timezone.utc),
        )
        assert isinstance(report.generated_at, datetime)

    def test_to_legacy_commodities_preserves_shape(self):
        """Legacy shape must have all original CommodityStatus fields."""
        ec = EnrichedCommodity(
            commodity=Commodity(
                id="test", name="Test Kit", unit="kits",
                mapping_status=MappingStatus.MAPPED,
            ),
            snapshot=StockSnapshot(consumed=90, stockout_days=2, stock_on_hand=300, period_days=30),
            metrics=StockMetrics(days_of_use=100, status=StockStatus.OK),
            forecast=ForecastResult(commodity_id="test"),
        )
        report = SupplyReport(
            org_unit="ou123",
            org_unit_name="Test",
            period="202401",
            generated_at=datetime.now(timezone.utc),
            commodities=[ec],
        )
        rows = report.to_legacy_commodities()
        assert len(rows) == 1
        row = rows[0]
        assert "commodity" in row
        assert "consumed" in row
        assert "stockout_days" in row
        assert "stock_on_hand" in row
        assert "days_of_use" in row
        assert "status" in row


class TestServiceCacheSync:
    """Verify cache calls are synchronous (no await on SessionCache)."""

    def test_session_cache_get_is_sync(self):
        from app.core.cache import SessionCache, InMemoryCache
        cache = SessionCache("test", InMemoryCache())
        # Must be callable without await
        result = cache.get("nonexistent")
        assert result is None

    def test_session_cache_set_is_sync(self):
        from app.core.cache import SessionCache, InMemoryCache
        cache = SessionCache("test", InMemoryCache())
        cache.set("key", "value", ttl=60)
        assert cache.get("key") == "value"
