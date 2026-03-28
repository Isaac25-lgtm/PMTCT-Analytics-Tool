"""
Stock data validation rules.

Produces warnings / errors / info without crashing the page.
"""

from __future__ import annotations

from app.supply.models import (
    Commodity,
    StockSnapshot,
    ValidationFinding,
    ValidationResult,
    ValidationSeverity,
)


def validate_snapshot(commodity: Commodity, snapshot: StockSnapshot) -> list[ValidationFinding]:
    """Run all validation checks on a single commodity snapshot."""
    findings: list[ValidationFinding] = []
    cid = commodity.id

    # Negative values
    for field_name, value in [
        ("consumed", snapshot.consumed),
        ("stock_on_hand", snapshot.stock_on_hand),
        ("expired", snapshot.expired),
        ("stockout_days", snapshot.stockout_days),
    ]:
        if value is not None and value < 0:
            findings.append(ValidationFinding(
                commodity_id=cid,
                field_name=field_name,
                severity=ValidationSeverity.ERROR,
                message=f"{field_name} is negative ({value})",
            ))

    # Stockout days outside period bounds
    if snapshot.stockout_days is not None:
        if snapshot.stockout_days > snapshot.period_days:
            findings.append(ValidationFinding(
                commodity_id=cid,
                field_name="stockout_days",
                severity=ValidationSeverity.ERROR,
                message=f"Stockout days ({snapshot.stockout_days}) exceeds period length ({snapshot.period_days})",
            ))

    # High stockout days but positive stock on hand
    if (
        snapshot.stockout_days is not None
        and snapshot.stock_on_hand is not None
        and snapshot.stockout_days > 14
        and snapshot.stock_on_hand > 0
    ):
        findings.append(ValidationFinding(
            commodity_id=cid,
            field_name="stockout_days",
            severity=ValidationSeverity.WARNING,
            message=f"High stockout days ({snapshot.stockout_days}) with positive SOH ({snapshot.stock_on_hand})",
        ))

    # Expired > consumed + SOH
    if (
        snapshot.expired is not None
        and snapshot.consumed is not None
        and snapshot.stock_on_hand is not None
        and snapshot.expired > snapshot.consumed + snapshot.stock_on_hand
    ):
        findings.append(ValidationFinding(
            commodity_id=cid,
            field_name="expired",
            severity=ValidationSeverity.WARNING,
            message="Expired quantity exceeds consumed + stock on hand",
        ))

    return findings


def validate_all(
    commodity_snapshots: list[tuple[Commodity, StockSnapshot]],
) -> ValidationResult:
    """Run validations on all commodity snapshots."""
    all_findings: list[ValidationFinding] = []
    for commodity, snapshot in commodity_snapshots:
        all_findings.extend(validate_snapshot(commodity, snapshot))
    return ValidationResult(findings=all_findings)
