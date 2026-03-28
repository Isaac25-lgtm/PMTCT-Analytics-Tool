"""
Pydantic models for DHIS2 API responses.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class DataValue(BaseModel):
    """Single data value from DHIS2."""
    data_element: str = Field(alias="dataElement")
    period: str
    org_unit: str = Field(alias="orgUnit")
    category_option_combo: Optional[str] = Field(None, alias="categoryOptionCombo")
    attribute_option_combo: Optional[str] = Field(None, alias="attributeOptionCombo")
    value: str
    stored_by: Optional[str] = Field(None, alias="storedBy")
    created: Optional[datetime] = None
    last_updated: Optional[datetime] = Field(None, alias="lastUpdated")

    @property
    def numeric_value(self) -> Optional[float]:
        try:
            return float(self.value)
        except (ValueError, TypeError):
            return None

    model_config = {"populate_by_name": True}


class DataValueSet(BaseModel):
    """Collection of data values."""
    values: List[DataValue] = Field(default_factory=list)

    @classmethod
    def from_dhis2_response(cls, response: Dict[str, Any]) -> "DataValueSet":
        return cls(values=[DataValue(**dv) for dv in response.get("dataValues", [])])

    def get_value(
        self,
        data_element: str,
        category_option_combo: str = None,
    ) -> Optional[float]:
        for dv in self.values:
            if dv.data_element == data_element:
                if category_option_combo is None or dv.category_option_combo == category_option_combo:
                    return dv.numeric_value
        return None

    def to_dict(self) -> Dict[str, float]:
        result: Dict[str, float] = {}
        for dv in self.values:
            key = dv.data_element
            if dv.category_option_combo:
                key = f"{key}.{dv.category_option_combo}"
            if dv.numeric_value is not None:
                result[key] = dv.numeric_value
        return result


class OrgUnit(BaseModel):
    """Organisation unit."""
    uid: str = Field(alias="id")
    name: str
    level: Optional[int] = None
    parent_uid: Optional[str] = None
    parent_name: Optional[str] = None
    children: List["OrgUnit"] = Field(default_factory=list)

    @classmethod
    def from_dhis2_response(cls, response: Dict[str, Any]) -> "OrgUnit":
        parent = response.get("parent", {})
        return cls(
            uid=response.get("id"),
            name=response.get("name"),
            level=response.get("level"),
            parent_uid=parent.get("id"),
            parent_name=parent.get("name"),
            children=[
                cls.from_dhis2_response(c)
                for c in response.get("children", [])
            ],
        )

    model_config = {"populate_by_name": True}


class CompletionStatus(BaseModel):
    """Dataset completion status."""
    org_unit: str
    period: str
    is_complete: bool
    completed_date: Optional[datetime] = None
    completed_by: Optional[str] = None

    total_expected: int = 1
    total_complete: int = 0

    @property
    def completion_rate(self) -> float:
        if self.total_expected == 0:
            return 0.0
        return (self.total_complete / self.total_expected) * 100

    @classmethod
    def from_dhis2_response(
        cls,
        response: Dict[str, Any],
        org_unit: str,
        period: str,
        expected_count: int = 1,
    ) -> "CompletionStatus":
        registrations = response.get("completeDataSetRegistrations", [])
        actual_complete = len(registrations)

        if not registrations:
            return cls(
                org_unit=org_unit,
                period=period,
                is_complete=False,
                total_expected=expected_count,
                total_complete=0,
            )

        first_reg = registrations[0]
        return cls(
            org_unit=org_unit,
            period=period,
            is_complete=actual_complete >= expected_count,
            completed_date=first_reg.get("date"),
            completed_by=first_reg.get("storedBy"),
            total_expected=expected_count,
            total_complete=actual_complete,
        )


class AnalyticsRow(BaseModel):
    """Single row from analytics response."""
    data: Dict[str, Any] = Field(default_factory=dict)

    def get(self, key: str) -> Optional[Any]:
        return self.data.get(key)


class AnalyticsResponse(BaseModel):
    """Analytics API response."""
    headers: List[Dict[str, str]] = Field(default_factory=list)
    rows: List[AnalyticsRow] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_dhis2_response(cls, response: Dict[str, Any]) -> "AnalyticsResponse":
        headers = response.get("headers", [])
        header_names = [h.get("name") for h in headers]

        rows = []
        for row_data in response.get("rows", []):
            row_dict = dict(zip(header_names, row_data))
            rows.append(AnalyticsRow(data=row_dict))

        return cls(
            headers=headers,
            rows=rows,
            metadata=response.get("metaData", {}),
        )

    def to_dataframe(self):
        import pandas as pd
        return pd.DataFrame([r.data for r in self.rows])


class DataElementMeta(BaseModel):
    """Data element metadata for validation."""
    uid: str = Field(alias="id")
    name: str
    short_name: Optional[str] = Field(None, alias="shortName")
    value_type: str = Field(alias="valueType")
    aggregation_type: str = Field(alias="aggregationType")
    category_combo_id: Optional[str] = None
    category_combo_name: Optional[str] = None

    @classmethod
    def from_dhis2_response(cls, response: Dict[str, Any]) -> "DataElementMeta":
        cat_combo = response.get("categoryCombo", {})
        return cls(
            uid=response.get("id"),
            name=response.get("name"),
            short_name=response.get("shortName"),
            value_type=response.get("valueType"),
            aggregation_type=response.get("aggregationType"),
            category_combo_id=cat_combo.get("id"),
            category_combo_name=cat_combo.get("name"),
        )

    model_config = {"populate_by_name": True}


class CategoryOptionComboMeta(BaseModel):
    """Category option combo metadata."""
    uid: str = Field(alias="id")
    name: str
    category_combo_id: Optional[str] = None
    category_combo_name: Optional[str] = None
    category_options: List[Dict[str, str]] = Field(default_factory=list)

    @classmethod
    def from_dhis2_response(cls, response: Dict[str, Any]) -> "CategoryOptionComboMeta":
        cat_combo = response.get("categoryCombo", {})
        return cls(
            uid=response.get("id"),
            name=response.get("name"),
            category_combo_id=cat_combo.get("id"),
            category_combo_name=cat_combo.get("name"),
            category_options=response.get("categoryOptions", []),
        )

    model_config = {"populate_by_name": True}
