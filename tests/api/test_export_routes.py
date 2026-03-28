"""
API tests for export routes.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


def _mock_build_cached_connector(*args, **kwargs):
    connector = AsyncMock()
    connector.get_data_values = AsyncMock(return_value={})
    connector.__aenter__ = AsyncMock(return_value=connector)
    connector.__aexit__ = AsyncMock(return_value=False)
    return connector


@pytest.mark.api
class TestScorecardExport:
    def test_export_pdf(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        with patch(
            "app.api.routes.exports.export_service.export_scorecard",
            return_value=b"pdf-data",
        ), patch(
            "app.api.routes.exports.export_service.get_filename",
            return_value="scorecard.pdf",
        ), patch(
            "app.api.routes.exports.export_service.get_content_type",
            return_value="application/pdf",
        ):
            response = client.post(
                "/api/exports/scorecard",
                json={"org_unit": "akV6429SUqu", "period": "202401", "format": "pdf"},
            )

        assert response.status_code == 200
        assert response.content == b"pdf-data"
        assert response.headers["content-type"].startswith("application/pdf")
        assert "attachment" in response.headers["content-disposition"]

    def test_export_xlsx(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        with patch(
            "app.api.routes.exports.export_service.export_scorecard",
            return_value=b"xlsx-data",
        ), patch(
            "app.api.routes.exports.export_service.get_filename",
            return_value="scorecard.xlsx",
        ), patch(
            "app.api.routes.exports.export_service.get_content_type",
            return_value="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ):
            response = client.post(
                "/api/exports/scorecard",
                json={"org_unit": "akV6429SUqu", "period": "202401", "format": "xlsx"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        )

    def test_export_scorecard_accepts_period_range_and_population(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        with patch(
            "app.api.routes.exports.export_service.export_scorecard",
            return_value=b"xlsx-data",
        ), patch(
            "app.api.routes.exports.export_service.get_filename",
            return_value="scorecard.xlsx",
        ), patch(
            "app.api.routes.exports.export_service.get_content_type",
            return_value="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ):
            response = client.post(
                "/api/exports/scorecard",
                json={
                    "org_unit": "akV6429SUqu",
                    "period_start": "202401",
                    "period_end": "202403",
                    "annual_population": 100000,
                    "format": "xlsx",
                },
            )

        assert response.status_code == 200
        mock_calculator.set_expected_pregnancies.assert_called_with("akV6429SUqu", 1250)

    def test_export_csv(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        with patch(
            "app.api.routes.exports.export_service.export_scorecard",
            return_value=b"csv-data",
        ), patch(
            "app.api.routes.exports.export_service.get_filename",
            return_value="scorecard.csv",
        ), patch(
            "app.api.routes.exports.export_service.get_content_type",
            return_value="text/csv; charset=utf-8",
        ):
            response = client.post(
                "/api/exports/scorecard",
                json={"org_unit": "akV6429SUqu", "period": "202401", "format": "csv"},
            )

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")

    def test_export_invalid_format_returns_422(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        response = client.post(
            "/api/exports/scorecard",
            json={"org_unit": "akV6429SUqu", "period": "202401", "format": "excel"},
        )

        assert response.status_code == 422


@pytest.mark.api
class TestCascadeExport:
    def test_export_cascade_pdf(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        with patch(
            "app.api.routes.exports.export_service.export_cascade",
            return_value=b"pdf-data",
        ), patch(
            "app.api.routes.exports.export_service.get_filename",
            return_value="cascade.pdf",
        ), patch(
            "app.api.routes.exports.export_service.get_content_type",
            return_value="application/pdf",
        ):
            response = client.post(
                "/api/exports/cascade",
                json={
                    "org_unit": "akV6429SUqu",
                    "period": "202401",
                    "cascade_type": "hiv",
                    "format": "pdf",
                },
            )

        assert response.status_code == 200


@pytest.mark.api
class TestSupplyExport:
    def test_export_supply_xlsx(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        with patch(
            "app.supply.service.build_cached_connector",
            _mock_build_cached_connector,
        ), patch(
            "app.api.routes.exports.export_service.export_supply",
            return_value=b"xlsx-data",
        ), patch(
            "app.api.routes.exports.export_service.get_filename",
            return_value="supply.xlsx",
        ), patch(
            "app.api.routes.exports.export_service.get_content_type",
            return_value="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ):
            response = client.post(
                "/api/exports/supply",
                json={"org_unit": "akV6429SUqu", "period": "202401", "format": "xlsx"},
            )

        assert response.status_code == 200
        assert "attachment" in response.headers["content-disposition"]

    def test_export_supply_accepts_period_range(
        self,
        client,
        valid_session,
        mock_calculator,
        override_dependencies,
    ) -> None:
        override_dependencies(session=valid_session, calculator=mock_calculator)

        with patch(
            "app.supply.service.build_cached_connector",
            _mock_build_cached_connector,
        ), patch(
            "app.api.routes.exports.export_service.export_supply",
            return_value=b"xlsx-data",
        ), patch(
            "app.api.routes.exports.export_service.get_filename",
            return_value="supply.xlsx",
        ), patch(
            "app.api.routes.exports.export_service.get_content_type",
            return_value="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ):
            response = client.post(
                "/api/exports/supply",
                json={
                    "org_unit": "akV6429SUqu",
                    "period_start": "202401",
                    "period_end": "202403",
                    "periodicity": "monthly",
                    "format": "xlsx",
                },
            )

        assert response.status_code == 200
