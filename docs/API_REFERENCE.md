# API Reference

## Overview

The application exposes JSON APIs for authentication, analytics, exports, diagnostics, and supporting UI workflows. Many reporting endpoints also serve HTMX partials when the request includes `HX-Request: true`.

Base URL examples below assume the app is mounted at the domain root.

## Authentication

### `POST /auth/login`

Authenticate with DHIS2 and create an application session.

Request body:

```json
{
  "dhis2_url": "https://hmis.health.go.ug",
  "auth_method": "basic",
  "username": "user@example.org",
  "password": "secret"
}
```

PAT login is also supported:

```json
{
  "dhis2_url": "https://hmis.health.go.ug",
  "auth_method": "pat",
  "pat_token": "dhis2-pat"
}
```

### `GET /auth/status`

Return the current browser-session authentication state.

### `POST /auth/logout`

Destroy the active session.

### `POST /auth/refresh`

Refresh the expiry time of the current session.

## Indicators

### `GET /api/indicators/`

List indicator definitions. Optional query parameter:

- `category`

### `GET /api/indicators/categories`

List supported indicator categories.

### `POST /api/indicators/calculate`

Calculate one or more indicators for a selected organisation unit and period.

Request body:

```json
{
  "org_unit": "akV6429SUqu",
  "period": "202401",
  "categories": ["who_validation"]
}
```

Optional fields:

- `org_unit_name`
- `include_children`
- `indicator_ids`
- `expected_pregnancies`

### `GET /api/indicators/calculate/{indicator_id}`

Calculate a single indicator via query parameters:

- `org_unit`
- `period`
- optional `org_unit_name`
- optional `include_children`
- optional `expected_pregnancies`

### `GET /api/indicators/{indicator_id}`

Return one indicator definition.

## Reports

### `POST /api/reports/scorecard`

Generate the WHO validation scorecard.

### `POST /api/reports/cascade`

Generate one cascade report. Request body includes:

- `org_unit`
- `period`
- `cascade_type` as `hiv`, `hbv`, or `syphilis`

### `POST /api/reports/supply-status`

Generate the supply status report. JSON callers receive a backward-compatible `commodities` list plus enriched supply metadata when available.

### `GET /api/reports/org-units`

Return organisation units attached to the active session.

### `GET /api/reports/periods`

Return selectable period options for the UI. Query parameters:

- `periodicity`
- `history_depth`
- optional `count`

## Exports

Exports are POST endpoints with JSON request bodies.

### `POST /api/exports/scorecard`

Body:

```json
{
  "format": "pdf",
  "org_unit": "akV6429SUqu",
  "period": "202401"
}
```

### `POST /api/exports/cascade`

Body fields:

- `format`
- `org_unit`
- `period`
- `cascade_type`

### `POST /api/exports/supply`

Body fields:

- `format`
- `org_unit`
- `period`

Supported formats:

- `pdf`
- `xlsx`
- `csv`

## Trends

### `POST /api/trends/analyze`

Analyze monthly trends for selected indicators.

Request body:

```json
{
  "indicator_ids": ["VAL-01", "VAL-02"],
  "org_unit": "akV6429SUqu",
  "end_period": "202401",
  "num_periods": 6
}
```

### `GET /api/trends/periods`

Return recent monthly periods for the trends page.

## Data quality

### `POST /api/data-quality/check`

Run DQ checks.

### `GET /api/data-quality/score`

Return overall DQ score for one organisation unit and period.

### `GET /api/data-quality/rules`

List configured DQ rules.

## Alerts

### `GET /api/alerts`

Return evaluated monthly alerts. Query parameters:

- `org_unit`
- `period`
- optional `severity`
- optional `category`
- optional `include_acknowledged`

### `GET /api/alerts/summary`

Return monthly alert summary only.

### `POST /api/alerts/{alert_id}/acknowledge`

Mark an alert as acknowledged for the active browser session.

### `GET /api/alerts/thresholds`

Return configured alert thresholds.

## Insights

All insight endpoints are POST routes under `/api/insights`.

- `/api/insights/indicator`
- `/api/insights/cascade`
- `/api/insights/alerts`
- `/api/insights/data-quality`
- `/api/insights/executive-summary`
- `/api/insights/recommendations`
- `/api/insights/qa`

Access requires the `use_ai_insights` permission.

## Organisation units

All organisation-unit routes are mounted under `/api/org-units`.

### `GET /api/org-units/roots`

Return accessible root nodes.

### `GET /api/org-units/search`

Search accessible nodes. Query parameters:

- `q`
- optional `root_uid`
- optional `max_results`

### `GET /api/org-units/{uid}`

Return one organisation unit with hierarchy context.

### `GET /api/org-units/{uid}/children`

Return accessible children. Optional query parameter:

- `include_parent`

### `GET /api/org-units/{uid}/breadcrumbs`

Return breadcrumb context.

## Health

### `GET /health/live`

Liveness probe.

### `GET /health/ready`

Readiness probe.

### `GET /health/startup`

Startup-complete marker used for deployment readiness checks.

### `GET /health`

Composite health summary.

### `GET /health/cache`

Cache snapshot.

### `GET /health/stats`

Extended runtime stats.

## Admin

Admin endpoints require `system_admin`.

### `GET /admin`

HTML admin dashboard.

### `GET /admin/status`

Return diagnostics and DHIS2 reachability.

### `GET /admin/cache`

Return detailed app and session cache stats.

### `POST /admin/cache/clear`

Clear all caches or one namespace. Optional query parameter:

- `namespace`

### `GET /admin/config/validate`

Validate repository configuration files.

### `GET /admin/sessions`

Return active session summaries.

### `POST /admin/sessions/{session_id}/terminate`

Terminate another in-memory session.

## Error handling

Typical status codes:

- `400` invalid request semantics
- `401` unauthenticated
- `403` forbidden
- `404` not found
- `422` validation error
- `429` rate limited
- `500` server error

Most JSON errors follow the FastAPI `detail` convention:

```json
{
  "detail": "Error message"
}
```
