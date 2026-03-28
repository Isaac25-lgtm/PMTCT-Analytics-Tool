"""
Supply-specific alert helpers.

Mirrors the Prompt 10 supply alert semantics exactly -- does NOT replace
the global AlertEngine.  These helpers enrich the supply page/report.
"""

from __future__ import annotations

from app.supply.commodities import get_thresholds
from app.supply.models import (
    AlertSeverity,
    EnrichedCommodity,
    SupplyAlert,
)


def generate_commodity_alerts(ec: EnrichedCommodity) -> list[SupplyAlert]:
    """Generate supply alerts for a single enriched commodity."""
    alerts: list[SupplyAlert] = []
    thresholds = get_thresholds()
    cid = ec.commodity.id
    name = ec.commodity.name
    dou = ec.metrics.days_of_use
    soh = ec.snapshot.stock_on_hand
    stockout_days = ec.snapshot.stockout_days
    expired = ec.snapshot.expired

    # Stockout
    if soh is not None and soh <= 0:
        alerts.append(SupplyAlert(
            commodity_id=cid,
            commodity_name=name,
            severity=AlertSeverity.CRITICAL,
            alert_type="stockout",
            message=f"{name}: stock on hand is zero",
            current_value=soh,
            threshold_value=0,
        ))

    # Imminent stockout (DOU < 7)
    elif dou is not None and dou < thresholds["imminent_stockout_dou"]:
        alerts.append(SupplyAlert(
            commodity_id=cid,
            commodity_name=name,
            severity=AlertSeverity.CRITICAL,
            alert_type="imminent_stockout",
            message=f"{name}: only {dou:.0f} days of use remaining",
            current_value=dou,
            threshold_value=thresholds["imminent_stockout_dou"],
        ))

    # Low stock (DOU < 30)
    elif dou is not None and dou < thresholds["low_stock_dou"]:
        alerts.append(SupplyAlert(
            commodity_id=cid,
            commodity_name=name,
            severity=AlertSeverity.WARNING,
            alert_type="low_stock",
            message=f"{name}: {dou:.0f} days of use (below {thresholds['low_stock_dou']:.0f}-day threshold)",
            current_value=dou,
            threshold_value=thresholds["low_stock_dou"],
        ))

    # Overstock (DOU > 180)
    if dou is not None and dou > thresholds["overstock_dou"]:
        alerts.append(SupplyAlert(
            commodity_id=cid,
            commodity_name=name,
            severity=AlertSeverity.INFO,
            alert_type="overstock",
            message=f"{name}: {dou:.0f} days of use (possible overstock)",
            current_value=dou,
            threshold_value=thresholds["overstock_dou"],
        ))

    # Stockout days reported
    if stockout_days is not None and stockout_days > 0:
        alerts.append(SupplyAlert(
            commodity_id=cid,
            commodity_name=name,
            severity=AlertSeverity.WARNING,
            alert_type="stockout_days_reported",
            message=f"{name}: {stockout_days:.0f} stockout day(s) reported in period",
            current_value=stockout_days,
            threshold_value=0,
        ))

    # Expiry signal
    if expired is not None and expired > 0:
        alerts.append(SupplyAlert(
            commodity_id=cid,
            commodity_name=name,
            severity=AlertSeverity.INFO,
            alert_type="expiry_wastage",
            message=f"{name}: {expired:.0f} unit(s) expired in period",
            current_value=expired,
        ))

    # Reorder needed
    if ec.forecast.reorder_needed and ec.forecast.reorder_quantity:
        alerts.append(SupplyAlert(
            commodity_id=cid,
            commodity_name=name,
            severity=AlertSeverity.WARNING,
            alert_type="reorder_needed",
            message=f"{name}: reorder recommended - {ec.forecast.reorder_quantity:.0f} {ec.commodity.unit}",
            current_value=ec.forecast.reorder_quantity,
        ))

    return alerts
