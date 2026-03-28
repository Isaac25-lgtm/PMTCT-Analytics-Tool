"""Unit tests for configuration validation utilities."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.admin.config_validator import ConfigValidator


@pytest.mark.unit
class TestConfigValidator:
    def test_validate_all_returns_results(self) -> None:
        validator = ConfigValidator()

        results = validator.validate_all()

        assert results
        assert all("file" in item for item in results)
        assert all("valid" in item for item in results)

    def test_summarize_returns_counts(self) -> None:
        validator = ConfigValidator()

        summary = validator.summarize()

        assert "files_checked" in summary
        assert "error_count" in summary
        assert "warning_count" in summary

    def test_validate_missing_file(self, tmp_path: Path) -> None:
        validator = ConfigValidator()

        result = validator.validate_file(str(tmp_path / "missing.yaml"))

        assert result.valid is False
        assert "File not found" in result.errors

    def test_validate_invalid_yaml(self, tmp_path: Path) -> None:
        broken = tmp_path / "broken.yaml"
        broken.write_text("bad: [", encoding="utf-8")
        validator = ConfigValidator()

        result = validator.validate_file(str(broken))

        assert result.valid is False
        assert any("YAML parse error" in error for error in result.errors)
