"""
Typed models for the supply chain module.

These models enrich the existing CommodityStatus in reports.py without
replacing it.  The SupplyReport bundles everything the enhanced supply
page/export needs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class MappingStatus(str, Enum):
    MAPPED = "mapped"
    MAPPING_PENDING = "mapping_pending"


class StockStatus(str, Enum):
    OK = "ok"
    LOW = "low"
    CRITICAL = "critical"
    STOCKOUT = "stockout"
    OVERSTOCK = "overstock"
    UNKNOWN = "unknown"


class ValidationSeverity(str, Enum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class AlertSeverity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


# -- Commodity registry --

@dataclass
class CommodityMapping:
    """Links a commodity to DHIS2 mapping codes (not raw UIDs)."""
    consumed: Optional[str] = None       # e.g. SS40a
    stockout_days: Optional[str] = None  # e.g. SS40b
    stock_on_hand: Optional[str] = None  # e.g. SS40c
    expired: Optional[str] = None        # e.g. SS40d
    days_of_use_indicator: Optional[str] = None  # e.g. SUP-05
    consumed_indicator: Optional[str] = None     # e.g. SUP-01
    stockout_days_indicator: Optional[str] = None  # e.g. SUP-02


@dataclass
class Commodity:
    """A tracer commodity tracked by the PMTCT supply module."""
    id: str
    name: str
    unit: str
    mapping_status: MappingStatus
    mapping: CommodityMapping = field(default_factory=CommodityMapping)
    category: str = "pmtct"
    reorder_level_months: float = 2.0
    max_stock_months: float = 6.0


# -- Stock snapshot and metrics --

@dataclass
class StockSnapshot:
    """Raw values fetched for a single commodity in a single period."""
    consumed: Optional[float] = None
    stockout_days: Optional[float] = None
    stock_on_hand: Optional[float] = None
    expired: Optional[float] = None
    days_of_use: Optional[float] = None
    period_days: int = 30


@dataclass
class StockMetrics:
    """Derived metrics computed from a StockSnapshot."""
    average_daily_consumption: Optional[float] = None
    adjusted_adc: Optional[float] = None
    days_of_use: Optional[float] = None
    months_of_stock: Optional[float] = None
    status: StockStatus = StockStatus.UNKNOWN


# -- Forecasting --

@dataclass
class ForecastResult:
    """Projected stock position at one or more horizons."""
    commodity_id: str
    horizons: dict[int, float] = field(default_factory=dict)  # days -> projected SOH
    reorder_quantity: Optional[float] = None
    reorder_needed: bool = False
    confidence: str = "normal"  # normal | low | no_data


# -- Validation --

@dataclass
class ValidationFinding:
    """A single data-quality finding for supply data."""
    commodity_id: str
    field_name: str
    severity: ValidationSeverity
    message: str


@dataclass
class ValidationResult:
    """Aggregated validation output for all commodities."""
    findings: list[ValidationFinding] = field(default_factory=list)

    @property
    def error_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == ValidationSeverity.ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == ValidationSeverity.WARNING)


# -- Supply alerts --

@dataclass
class SupplyAlert:
    """A supply-specific alert aligned to Prompt 10 semantics."""
    commodity_id: str
    commodity_name: str
    severity: AlertSeverity
    alert_type: str
    message: str
    current_value: Optional[float] = None
    threshold_value: Optional[float] = None


# -- Enriched commodity row --

@dataclass
class EnrichedCommodity:
    """Everything known about one commodity for a single period."""
    commodity: Commodity
    snapshot: StockSnapshot
    metrics: StockMetrics
    forecast: ForecastResult
    validation: list[ValidationFinding] = field(default_factory=list)
    alerts: list[SupplyAlert] = field(default_factory=list)


# -- Full supply report --

@dataclass
class SupplyReport:
    """Top-level payload returned by the supply service."""
    org_unit: str
    org_unit_name: Optional[str]
    period: str
    generated_at: datetime
    commodities: list[EnrichedCommodity] = field(default_factory=list)
    unmapped_commodities: list[Commodity] = field(default_factory=list)
    summary: dict = field(default_factory=dict)

    def to_legacy_commodities(self) -> list[dict]:
        """Return a list matching the existing CommodityStatus shape."""
        rows = []
        for ec in self.commodities:
            rows.append({
                "commodity": ec.commodity.name,
                "consumed": ec.snapshot.consumed,
                "stockout_days": ec.snapshot.stockout_days,
                "stock_on_hand": ec.snapshot.stock_on_hand,
                "days_of_use": ec.metrics.days_of_use,
                "status": ec.metrics.status.value,
            })
        return rows
