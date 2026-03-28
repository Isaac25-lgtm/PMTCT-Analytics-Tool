"""
Commodity registry loader.

Reads config/commodities.yaml and returns typed Commodity objects.
Mapping codes are resolved to UIDs via the indicator registry at query time,
not at load time.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from app.supply.models import Commodity, CommodityMapping, MappingStatus

logger = logging.getLogger(__name__)

_COMMODITIES_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "commodities.yaml"
_THRESHOLDS_DEFAULTS = {
    "stockout_dou": 0,
    "imminent_stockout_dou": 7,
    "low_stock_dou": 30,
    "overstock_dou": 180,
}

_cached_config: dict[str, Any] | None = None


def _load_raw() -> dict[str, Any]:
    global _cached_config
    if _cached_config is not None:
        return _cached_config
    if not _COMMODITIES_PATH.exists():
        logger.warning("commodities.yaml not found at %s", _COMMODITIES_PATH)
        _cached_config = {}
        return _cached_config
    with _COMMODITIES_PATH.open("r", encoding="utf-8") as fh:
        _cached_config = yaml.safe_load(fh) or {}
    return _cached_config


def get_thresholds() -> dict[str, float]:
    raw = _load_raw()
    thresholds = raw.get("thresholds", {})
    merged = {**_THRESHOLDS_DEFAULTS, **thresholds}
    return {k: float(v) for k, v in merged.items()}


def load_commodities() -> list[Commodity]:
    """Return all configured tracer commodities."""
    raw = _load_raw()
    commodities: list[Commodity] = []
    for _key, data in raw.get("commodities", {}).items():
        mapping_data = data.get("mapping") or {}
        commodities.append(
            Commodity(
                id=data["id"],
                name=data["name"],
                unit=data.get("unit", "units"),
                category=data.get("category", "pmtct"),
                mapping_status=MappingStatus(data.get("mapping_status", "mapping_pending")),
                reorder_level_months=float(data.get("reorder_level_months", 2.0)),
                max_stock_months=float(data.get("max_stock_months", 6.0)),
                mapping=CommodityMapping(
                    consumed=mapping_data.get("consumed"),
                    stockout_days=mapping_data.get("stockout_days"),
                    stock_on_hand=mapping_data.get("stock_on_hand"),
                    expired=mapping_data.get("expired"),
                    days_of_use_indicator=mapping_data.get("days_of_use_indicator"),
                    consumed_indicator=mapping_data.get("consumed_indicator"),
                    stockout_days_indicator=mapping_data.get("stockout_days_indicator"),
                ),
            )
        )
    return commodities


def get_mapped_commodities() -> list[Commodity]:
    """Return only commodities with confirmed DHIS2 mappings."""
    return [c for c in load_commodities() if c.mapping_status == MappingStatus.MAPPED]


def get_unmapped_commodities() -> list[Commodity]:
    """Return commodities still awaiting DHIS2 mapping confirmation."""
    return [c for c in load_commodities() if c.mapping_status == MappingStatus.MAPPING_PENDING]


def reset_cache() -> None:
    """Clear the cached config -- used for testing."""
    global _cached_config
    _cached_config = None
