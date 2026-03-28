# Developer Guide

## Architecture

The PMTCT tool is a FastAPI application that keeps all state in memory and treats DHIS2 as the system of record.

Core layers:

- `app/api/routes/` for JSON and HTMX endpoints
- `app/templates/` for full pages and partials
- `app/connectors/` for DHIS2 API access
- `app/indicators/` for registry and calculation logic
- `app/services/` for alerts, data quality, trends, exports, and org-unit features
- `app/supply/` for enriched supply-chain logic
- `app/core/` for config, cache, session handling, pooling, and logging
- `app/auth/` for DHIS2 auth, RBAC, audit, and rate limiting

## Design constraints

### Stateless runtime
- no application database
- sessions stored only in memory
- cache stored only in memory
- restart or redeploy clears sessions and cache

### DHIS2 source of truth
- calculations fetch DHIS2 data on demand
- mappings live in `config/mappings.yaml`
- indicators live in `config/indicators.yaml`

### Single-worker MVP deployment
- production container runs with one worker
- this preserves in-memory session and cache correctness

## Local setup

```bash
cp .env.example .env
pip install -r requirements.txt -r requirements-dev.txt -r requirements-export.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Key entry points

### Application bootstrap
- `app.main:create_app`
- `app.main:lifespan`

### Request dependencies
- `app.api.deps.CurrentSession`
- `app.api.deps.Calculator`
- `app.api.deps.Connector`
- `app.api.deps.OrgUnitSvc`
- `app.api.deps.SessCache`
- `app.api.deps.RBAC`
- `app.api.deps.Audit`

### Primary page routes
- `/dashboard`
- `/indicators`
- `/cascade/{cascade_type}`
- `/supply`
- `/data-quality`
- `/alerts`
- `/trends`
- `/insights`
- `/admin`

### Primary report routes
- `POST /api/reports/scorecard`
- `POST /api/reports/cascade`
- `POST /api/reports/supply-status`

## Development patterns

### Reuse dependencies instead of manual object construction

Preferred route style:

```python
from fastapi import APIRouter
from app.api.deps import Calculator, CurrentSession

router = APIRouter()

@router.post("/example")
async def example(
    calculator: Calculator,
    session: CurrentSession,
):
    return {"session_id": session.session_id}
```

### Use session-scoped caching for derived results

```python
from app.core.cache_keys import make_key, get_cache_ttl

cache_key = make_key("feature", "result", {"org_unit": org_unit, "period": period})
payload = await session_cache.get_or_set_async(
    cache_key,
    lambda: expensive_async_call(),
    ttl=get_cache_ttl("aggregate"),
)
```

### Keep route contracts backward-compatible

Many routes serve both JSON and HTMX:

- JSON callers expect response models
- HTMX callers expect rendered partials

When extending these routes, keep existing JSON fields and template expectations intact.

### Audit security-sensitive actions

Use the existing audit helpers instead of ad hoc logging.

Examples:

- `audit.log_export(...)`
- `audit.log_permission_denied(...)`
- `audit.log_rate_limit_exceeded(...)`
- `audit.log_cache_cleared(...)`

## Tests

Test layout:

- `tests/unit/` for isolated logic
- `tests/api/` for route-level behavior
- `tests/integration/` for end-to-end flows with a mock DHIS2 server

Common fixtures already live in `tests/conftest.py`:

- `client`
- `authenticated_client`
- `valid_session`
- `mock_connector`
- `mock_calculator`
- `override_dependencies`

## Configuration files

Main repository config files:

- `config/indicators.yaml`
- `config/mappings.yaml`
- `config/commodities.yaml`
- `config/alert_thresholds.yaml`
- `config/dq_rules.yaml`
- `config/rbac.yaml`
- `config/cache.yaml`
- `config/populations.yaml`
- `config/scoring.yaml`
- `config/thresholds.yaml`
- `config/org_hierarchy.yaml`
- `config/production.yaml`

Run the validator after editing configs:

```bash
python scripts/validate_config.py --verbose
```

## Adding features safely

### New page
1. Add a page route in `app/api/routes/pages.py`.
2. Add or reuse a Jinja template in `app/templates/`.
3. Back the page with existing `/api/...` routes where possible.

### New API route
1. Choose the correct router module under `app/api/routes/`.
2. Reuse `require_permission(...)` or `require_role(...)`.
3. Add API tests.
4. If the route is expensive, add session-scoped caching.

### New indicator
1. Add it to `config/indicators.yaml`.
2. Ensure referenced mapping codes exist in `config/mappings.yaml`.
3. Add unit tests and route coverage as needed.

## Deployment notes

- Render deployment assets live in `Dockerfile`, `render.yaml`, and `scripts/start.sh`.
- Production logging can be JSON or console, controlled by env vars.
- The shared HTTP client is managed in `app/core/connection_pool.py` and should not be closed per request.

## Known operational limits

- no horizontal scaling support in the MVP
- no persistent acknowledgement or admin state
- no database-backed audit query interface
- mock/integration tests still depend on local Python tooling being available
