"""
Supply chain forecasting logic.

Computes adjusted ADC, months of stock, reorder quantity, and projected
stock-on-hand at configurable horizons.  Preserves alignment with the
existing Prompt 3 DOU formula (DOU = SOH / ADC).
"""

from __future__ import annotations

from typing import Optional

from app.supply.models import ForecastResult, StockMetrics, StockSnapshot, StockStatus


DEFAULT_FORECAST_HORIZONS = [30, 60, 90]


def compute_metrics(snapshot: StockSnapshot) -> StockMetrics:
    """
    Derive StockMetrics from a raw StockSnapshot.

    ADC calculation mirrors the Prompt 3 DOU formula:
        ADC = consumed / (period_days - stockout_days)
        DOU = SOH / ADC
    """
    consumed = snapshot.consumed
    stockout_days = snapshot.stockout_days or 0
    stock_on_hand = snapshot.stock_on_hand
    period_days = snapshot.period_days

    if consumed is None or stock_on_hand is None:
        return StockMetrics(status=StockStatus.UNKNOWN)

    # Raw ADC (may undercount demand if stockout occurred)
    raw_adc = consumed / period_days if period_days > 0 else 0

    # Adjusted ADC corrects for stockout days
    available_days = max(period_days - stockout_days, 1)
    adjusted_adc = consumed / available_days if available_days > 0 else 0

    # Days of use from SOH and adjusted ADC
    if adjusted_adc > 0:
        dou = stock_on_hand / adjusted_adc
    elif stock_on_hand > 0:
        dou = None  # stock exists but no consumption data
    else:
        dou = 0.0

    # Prefer calculator-derived DOU when it is already available from SUP-05/SUP-06.
    # Fall back to the locally recomputed value when the indicator result is absent.
    effective_dou = snapshot.days_of_use if snapshot.days_of_use is not None else dou

    # Months of stock should align with the effective DOU that the UI will show.
    mos: Optional[float] = None
    if effective_dou is not None:
        mos = round(effective_dou / 30, 1)

    status = _classify_status(effective_dou)

    return StockMetrics(
        average_daily_consumption=round(raw_adc, 2) if raw_adc else None,
        adjusted_adc=round(adjusted_adc, 2) if adjusted_adc else None,
        days_of_use=round(effective_dou, 1) if effective_dou is not None else None,
        months_of_stock=mos,
        status=status,
    )


def compute_forecast(
    commodity_id: str,
    snapshot: StockSnapshot,
    metrics: StockMetrics,
    *,
    reorder_months: float = 2.0,
    max_stock_months: float = 6.0,
    horizons: list[int] | None = None,
) -> ForecastResult:
    """Project stock at future horizons and compute reorder quantity."""
    horizons = horizons or DEFAULT_FORECAST_HORIZONS
    adc = metrics.adjusted_adc
    soh = snapshot.stock_on_hand

    if adc is None or adc <= 0 or soh is None:
        return ForecastResult(
            commodity_id=commodity_id,
            horizons={h: 0.0 for h in horizons},
            reorder_quantity=None,
            reorder_needed=False,
            confidence="no_data" if soh is None else "low",
        )

    projected: dict[int, float] = {}
    for h in horizons:
        projected[h] = max(0.0, round(soh - adc * h, 1))

    # Reorder point = reorder_months * 30 * ADC
    reorder_point = reorder_months * 30 * adc
    max_stock = max_stock_months * 30 * adc
    reorder_needed = soh <= reorder_point
    reorder_qty: Optional[float] = None
    if reorder_needed:
        reorder_qty = round(max(0, max_stock - soh), 0)

    return ForecastResult(
        commodity_id=commodity_id,
        horizons=projected,
        reorder_quantity=reorder_qty,
        reorder_needed=reorder_needed,
        confidence="normal",
    )


def _classify_status(dou: Optional[float]) -> StockStatus:
    """Classify stock status from DOU, aligned with Prompt 10 thresholds."""
    if dou is None:
        return StockStatus.UNKNOWN
    if dou <= 0:
        return StockStatus.STOCKOUT
    if dou < 7:
        return StockStatus.CRITICAL
    if dou < 30:
        return StockStatus.LOW
    if dou > 180:
        return StockStatus.OVERSTOCK
    return StockStatus.OK
