"""Validation helpers for YAML configuration files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from app.core.config import CONFIG_DIR


@dataclass(slots=True)
class ValidationResult:
    """Structured result for one config file."""

    file: str
    config_type: str
    exists: bool
    valid: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "file": self.file,
            "type": self.config_type,
            "exists": self.exists,
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


class ConfigValidator:
    """Validate the live repository config contracts."""

    CONFIG_FILES = [
        "alert_thresholds.yaml",
        "cache.yaml",
        "commodities.yaml",
        "dq_rules.yaml",
        "indicators.yaml",
        "mappings.yaml",
        "org_hierarchy.yaml",
        "populations.yaml",
        "production.yaml",
        "rbac.yaml",
        "scoring.yaml",
        "thresholds.yaml",
    ]

    def validate_all(self) -> list[dict[str, Any]]:
        """Validate all known config files."""
        return [self.validate_file(filename).to_dict() for filename in self.CONFIG_FILES]

    def summarize(self) -> dict[str, int | bool]:
        """Return a compact summary for dashboards and CLI output."""
        results = [self.validate_file(filename) for filename in self.CONFIG_FILES]
        return {
            "files_checked": len(results),
            "valid": all(result.valid for result in results),
            "error_count": sum(len(result.errors) for result in results),
            "warning_count": sum(len(result.warnings) for result in results),
        }

    def validate_file(self, filename: str) -> ValidationResult:
        """Validate a single config file by repository-relative name."""
        path = Path(filename)
        if not path.is_absolute():
            path = CONFIG_DIR / filename

        try:
            display_path = str(path.relative_to(CONFIG_DIR.parent))
        except ValueError:
            display_path = str(path)

        result = ValidationResult(
            file=display_path,
            config_type=path.stem,
            exists=path.exists(),
        )
        if not path.exists():
            result.errors.append("File not found")
            return result

        try:
            with path.open("r", encoding="utf-8") as handle:
                data = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            result.errors.append(f"YAML parse error: {exc}")
            return result
        except Exception as exc:
            result.errors.append(f"Unable to read file: {exc}")
            return result

        if data is None:
            result.errors.append("Configuration file is empty")
            return result

        validator = getattr(self, f"_validate_{path.stem}", None)
        if validator is not None:
            validator(data, result)

        result.valid = not result.errors
        return result

    def _validate_indicators(self, data: dict[str, Any], result: ValidationResult) -> None:
        indicators = data.get("indicators")
        if not isinstance(indicators, dict) or not indicators:
            result.errors.append("indicators.yaml must define a non-empty 'indicators' mapping")
            return

        for indicator_id, payload in indicators.items():
            if not isinstance(payload, dict):
                result.errors.append(f"{indicator_id}: indicator payload must be a mapping")
                continue
            if payload.get("id") != indicator_id:
                result.warnings.append(f"{indicator_id}: payload id does not match key")
            for field_name in ("name", "category", "result_type", "periodicity"):
                if not payload.get(field_name):
                    result.errors.append(f"{indicator_id}: missing '{field_name}'")
            if "numerator" not in payload and payload.get("calculation_type") is None:
                result.warnings.append(f"{indicator_id}: no numerator or custom calculation_type declared")

    def _validate_mappings(self, data: dict[str, Any], result: ValidationResult) -> None:
        data_elements = data.get("data_elements")
        if not isinstance(data_elements, dict) or not data_elements:
            result.errors.append("mappings.yaml must define a non-empty 'data_elements' mapping")
            return
        for code, payload in data_elements.items():
            if not isinstance(payload, dict) or not payload.get("uid"):
                result.errors.append(f"{code}: missing UID mapping")

    def _validate_commodities(self, data: dict[str, Any], result: ValidationResult) -> None:
        commodities = data.get("commodities")
        if not isinstance(commodities, dict) or not commodities:
            result.errors.append("commodities.yaml must define a non-empty 'commodities' mapping")
            return
        for commodity_id, payload in commodities.items():
            if payload.get("id") != commodity_id:
                result.warnings.append(f"{commodity_id}: payload id does not match key")
            if not payload.get("name"):
                result.errors.append(f"{commodity_id}: missing 'name'")
            mapping_status = payload.get("mapping_status")
            if mapping_status not in {"mapped", "mapping_pending"}:
                result.errors.append(f"{commodity_id}: invalid mapping_status '{mapping_status}'")
            mapping = payload.get("mapping", {})
            if mapping_status == "mapped" and not any(mapping.get(field) for field in ("consumed", "stockout_days", "stock_on_hand", "expired")):
                result.errors.append(f"{commodity_id}: mapped commodities need at least one data mapping")
            if mapping_status == "mapping_pending":
                result.warnings.append(f"{commodity_id}: DHIS2 mapping still pending")

    def _validate_alert_thresholds(self, data: dict[str, Any], result: ValidationResult) -> None:
        thresholds = data.get("thresholds")
        if not isinstance(thresholds, list) or not thresholds:
            result.errors.append("alert_thresholds.yaml must define a non-empty 'thresholds' list")
            return
        for threshold in thresholds:
            if not threshold.get("id"):
                result.errors.append("alert threshold missing 'id'")
            if not threshold.get("indicator_ids"):
                result.warnings.append(f"{threshold.get('id', 'unknown')}: no indicator_ids configured")

    def _validate_dq_rules(self, data: dict[str, Any], result: ValidationResult) -> None:
        rules = data.get("rules")
        if not isinstance(rules, list) or not rules:
            result.errors.append("dq_rules.yaml must define a non-empty 'rules' list")
            return
        for rule in rules:
            if not rule.get("id"):
                result.errors.append("DQ rule missing 'id'")
            if not rule.get("severity"):
                result.errors.append(f"{rule.get('id', 'unknown')}: missing severity")

    def _validate_rbac(self, data: dict[str, Any], result: ValidationResult) -> None:
        roles = data.get("roles")
        permissions = data.get("permissions")
        if not isinstance(roles, dict) or not roles:
            result.errors.append("rbac.yaml must define a non-empty 'roles' mapping")
        if not isinstance(permissions, dict) or not permissions:
            result.errors.append("rbac.yaml must define a non-empty 'permissions' mapping")

    def _validate_cache(self, data: dict[str, Any], result: ValidationResult) -> None:
        if not isinstance(data.get("cache"), dict):
            result.errors.append("cache.yaml must define a 'cache' mapping")
        if not isinstance(data.get("ttl"), dict):
            result.warnings.append("cache.yaml should define per-namespace TTL values under 'ttl'")

    def _validate_thresholds(self, data: dict[str, Any], result: ValidationResult) -> None:
        if not isinstance(data, dict) or not data:
            result.errors.append("thresholds.yaml must contain threshold groups")
            return
        if "coverage" not in data:
            result.warnings.append("thresholds.yaml is missing 'coverage' thresholds")
        if "stockout" not in data:
            result.warnings.append("thresholds.yaml is missing 'stockout' thresholds")

    def _validate_populations(self, data: dict[str, Any], result: ValidationResult) -> None:
        districts = data.get("districts")
        if not isinstance(districts, dict):
            result.errors.append("populations.yaml must define a 'districts' mapping")
            return
        if "PLACEHOLDER_DISTRICT_UID" in districts:
            result.warnings.append("populations.yaml still contains placeholder district data")
        national = data.get("national")
        if not isinstance(national, dict):
            result.warnings.append("populations.yaml should define a 'national' summary")

    def _validate_org_hierarchy(self, data: dict[str, Any], result: ValidationResult) -> None:
        hierarchy = data.get("hierarchy")
        if not isinstance(hierarchy, dict):
            result.errors.append("org_hierarchy.yaml must define a 'hierarchy' mapping")
            return
        if not isinstance(hierarchy.get("levels"), dict):
            result.errors.append("org_hierarchy.yaml must define hierarchy levels")

    def _validate_scoring(self, data: dict[str, Any], result: ValidationResult) -> None:
        weights = data.get("weights")
        if not isinstance(weights, dict) or not weights:
            result.errors.append("scoring.yaml must define a non-empty 'weights' mapping")
            return
        total = sum(float(value) for value in weights.values())
        if abs(total - 1.0) > 0.001:
            result.warnings.append(f"scoring weights sum to {total:.3f}, expected 1.000")

    def _validate_production(self, data: dict[str, Any], result: ValidationResult) -> None:
        if not isinstance(data, dict) or not data:
            result.errors.append("production.yaml must contain override sections")
            return
        if "logging" not in data:
            result.warnings.append("production.yaml should define logging overrides")
        if "cache" not in data:
            result.warnings.append("production.yaml should define cache overrides")
