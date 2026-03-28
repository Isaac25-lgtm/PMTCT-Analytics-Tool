# PMTCT Triple Elimination Analytics Tool

Stateless, DHIS2-connected analytics dashboard for monitoring Uganda's PMTCT Triple Elimination programme across **HIV**, **syphilis**, and **hepatitis B** — built for the Ministry of Health.

The tool connects directly to Uganda's Health Management Information System (DHIS2) at the point of use. It does not store patient data, does not maintain a local database, and does not persist credentials beyond the active browser session.

---

## What It Does

The tool pulls live data from DHIS2 and computes **30 programme indicators** across six categories:

| Category | Indicators | Coverage |
|---|---|---|
| WHO Validation | VAL-01 through VAL-06 | ANC coverage, first trimester attendance, testing uptake benchmarks |
| HIV Cascade | HIV-01 through HIV-10 | Testing, positivity, ART initiation, early infant diagnosis, viral suppression |
| Hepatitis B | HBV-01 through HBV-08 | HBsAg screening, positivity rate, birth dose coverage |
| Syphilis | SYP-01 | Testing, positivity, treatment completion |
| Supply Chain | SUP-01 through SUP-06 | Consumption, stockout days, days-of-use for tracer commodities |
| System | SYS-01, SYS-03 | Reporting completeness, data quality composite score |

It maps **49 DHIS2 data elements** and **5 category option combos** from HMIS 105 (ANC, Maternity, EID, Immunization) and HMIS 033B (weekly surveillance, stock reporting) to these indicators using config-driven YAML definitions.

On top of the raw numbers, the tool provides:

- **WHO validation scorecards** with period ranges, child org-unit comparison, and composite risk scoring
- **Data quality checks** using configurable rule sets (consistency, completeness, outlier detection)
- **Alert engine** with threshold-based triggers for coverage drops, stockouts, and missed appointments
- **Trend analysis** across monthly periods with directional indicators
- **AI-generated insights** (optional) via a vendor-neutral LLM integration supporting Anthropic, OpenAI, and Azure OpenAI
- **Excel and PDF exports** for offline reporting and district review meetings
- **Org-unit hierarchy navigation** with search, breadcrumbs, and drill-down from national to facility level

---

## Architecture

```
+--------------------------------------------------+
|                    Browser                        |
|         Jinja2 + HTMX + Chart.js + Tailwind      |
+------------------+-------------------------------+
                   |  Session cookie (httponly, secure)
+------------------v-------------------------------+
|               FastAPI Application                 |
|                                                   |
|  +---------+ +----------+ +-------------------+  |
|  |  Auth   | | RBAC +   | |   Middleware       |  |
|  | (DHIS2) | | Audit    | | CSRF / Rate Limit  |  |
|  +----+----+ +----------+ | Security Headers   |  |
|       |                   | Request Logging    |  |
|  +----v------------------+-------------------+   |
|  |   DHIS2 Connector     |                        |
|  |  (httpx, async,       |  +------------------+  |
|  |   connection pool,    |  |  Indicator Engine |  |
|  |   retry + backoff)    +-->  30 indicators   |  |
|  +-----------------------+  |  YAML-driven     |  |
|                             +--------+---------+  |
|  +--------------+  +------------+    |            |
|  | Alert Engine |  | DQ Engine  |<---+            |
|  +--------------+  +------------+                 |
|  +--------------+  +------------+                 |
|  | AI Insights  |  |  Exports   |                 |
|  | (optional)   |  | Excel/PDF  |                 |
|  +--------------+  +------------+                 |
+--------------------------------------------------+
                   |
                   |  DHIS2 Web API (analytics, dataValueSets,
                   |  organisationUnits, completeDataSetRegistrations)
                   v
          +---------------------+
          |  Uganda HMIS        |
          |  DHIS2 Instance     |
          +---------------------+
```

**Key design decisions:**

- **Stateless.** Data is fetched on demand from DHIS2 and not stored locally. No database.
- **Session-only credentials.** Users authenticate with their DHIS2 account. Credentials exist in server memory for the session duration only and are cleared on logout or expiry.
- **Config-driven.** Indicator definitions, data element mappings, alert thresholds, scoring weights, and RBAC rules all live in YAML files under `config/`. Adapting to a different DHIS2 instance means editing config, not code.
- **Single-worker MVP.** Sessions, caches, and rate-limit state are process-local. The startup script enforces `WEB_CONCURRENCY=1` and logs a warning if someone tries to change it.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | FastAPI 0.115, Python 3.11, httpx (async, HTTP/2, connection pooling) |
| Data | pandas, numpy, pydantic, pydantic-settings |
| Frontend | Jinja2 templates, HTMX partials, Chart.js, Tailwind CSS |
| Exports | openpyxl (Excel), WeasyPrint (PDF) |
| AI (optional) | Vendor-neutral -- Anthropic, OpenAI, or Azure OpenAI via raw httpx |
| Auth | DHIS2-delegated (Basic Auth + PAT), server-side sessions, CSRF tokens |
| Security | Rate limiting, audit logging, security headers (CSP, HSTS, X-Frame-Options) |
| Deployment | Docker (multi-stage), Render, GitHub Actions CI/CD |

---

## Quick Start

### Local development

```bash
# Clone
git clone https://github.com/Isaac25-lgtm/PMTCT-Analytics-Tool.git
cd PMTCT-Analytics-Tool

# Configure
cp .env.example .env
# Edit .env -- at minimum set DHIS2_BASE_URL to your DHIS2 instance

# Install
pip install -r requirements.txt -r requirements-dev.txt -r requirements-export.txt

# Run
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` and log in with your DHIS2 credentials.

### Docker

```bash
docker compose up --build
```

The app will be available at `http://localhost:8000` with hot-reload enabled via volume mounts.

---

## Configuration

All DHIS2 metadata and application behaviour is controlled through YAML files in `config/`:

| File | Purpose |
|---|---|
| `mappings.yaml` | 49 data element UIDs + 5 category option combo UIDs mapped to internal codes |
| `indicators.yaml` | 30 indicator definitions with formulas, targets, and result types |
| `populations.yaml` | UBOS district population estimates for coverage denominators |
| `commodities.yaml` | Tracer commodity registry (HBsAg kits, duo kits, etc.) with reorder levels |
| `alert_thresholds.yaml` | Coverage, stockout, data quality, and missed appointment thresholds |
| `dq_rules.yaml` | Data quality validation rules (consistency, completeness, outliers) |
| `scoring.yaml` | Composite risk score weights (12 dimensions, must sum to 1.0) |
| `rbac.yaml` | Role definitions, permission mappings, and rate limit rules |
| `cache.yaml` | TTL settings for different cache categories and connection pool tuning |

**Adapting to a different DHIS2 instance:** Update the UIDs in `mappings.yaml` to match the target instance's data element and category option combo identifiers. The indicator formulas reference internal codes (e.g., `AN01a`, `SS40c`), not raw UIDs, so only the mapping layer needs to change.

---

## Deployment

### Render (primary target)

The repo includes a `render.yaml` Blueprint that defines production and staging services:

```
Production: Docker service on main branch, standard plan
Staging: Docker service on staging branch, starter plan
SECRET_KEY is auto-generated by Render
```

Required environment variables on Render:
- `DHIS2_BASE_URL` -- Target DHIS2 instance (e.g., `https://hmis.health.go.ug`)
- `SECRET_KEY` -- Auto-generated via `render.yaml`
- `LLM_API_KEY` + `LLM_MODEL` -- Optional, for AI insights

The Dockerfile uses a multi-stage build, runs as a non-root user, includes a health check (`/health/ready`), and uses `tini` as the init process.

### CI/CD

GitHub Actions workflows in `.github/workflows/`:
- **CI** -- Linting (ruff), type checking (mypy), unit tests, API tests, integration tests, Docker build smoke test, and security scanning (bandit, pip-audit)
- **Deploy** -- Triggers Render deployment via API after CI passes, polls for completion, and runs a health check against the live URL

Required GitHub Secrets for deployment:
- `RENDER_API_KEY`
- `RENDER_SERVICE_ID_PROD`
- `RENDER_SERVICE_ID_STAGING`

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for full operational guidance.

---

## Project Structure

```
PMTCT-Analytics-Tool/
|-- app/
|   |-- main.py                  # FastAPI factory, lifespan, middleware wiring
|   |-- api/
|   |   |-- deps.py              # Dependency injection (session, calculator)
|   |   |-- middleware.py         # Session, CSRF, rate limit, security headers, logging
|   |   |-- schemas.py           # Shared API schemas
|   |   +-- routes/
|   |       |-- auth.py          # DHIS2 login/logout/status/refresh
|   |       |-- indicators.py    # Indicator calculation endpoints
|   |       |-- reports.py       # WHO scorecards, cascades, comparisons
|   |       |-- data_quality.py  # DQ checks and scoring
|   |       |-- alerts.py        # Alert evaluation and summaries
|   |       |-- trends.py        # Multi-period trend analysis
|   |       |-- insights.py      # AI-generated insight endpoints
|   |       |-- exports.py       # Excel and PDF export
|   |       |-- org_units.py     # Hierarchy navigation and search
|   |       |-- pages.py         # HTMX page routes
|   |       |-- health.py        # Liveness, readiness, startup, cache, stats
|   |       +-- admin.py         # Diagnostics and config validation
|   |-- auth/                    # DHIS2 auth, RBAC, roles, permissions, audit, rate limiting
|   |-- connectors/              # DHIS2 API connector (async, pooled, retry) + cached wrapper
|   |-- core/                    # Config, session manager, cache, connection pool, logging
|   |-- indicators/              # Registry, calculator, models, cached calculator
|   |-- services/                # AI insights, alerts, DQ, trends, exports, LLM providers
|   |-- supply/                  # Commodity tracking, forecasting, validation, alerts
|   |-- reports/                 # Excel and PDF generators
|   |-- admin/                   # Config validator, system diagnostics
|   |-- analytics/               # Anomaly detection, risk scoring, trajectory analysis
|   |-- templates/               # 14 Jinja2 pages + 25 HTMX component partials
|   |-- utils/                   # Temp file management
|   +-- validation/              # Validation rule engine
|-- config/                      # 12 YAML configuration files
|-- static/                      # CSS + JS (Tailwind, Chart.js, HTMX wiring)
|-- scripts/                     # start.sh, healthcheck.sh, validate_config.py
|-- tests/                       # Unit, API, and integration test suites
|-- docs/                        # User guide, API reference, developer guide, deployment
|-- Dockerfile                   # Multi-stage production image
|-- docker-compose.yml           # Local development with hot-reload
|-- render.yaml                  # Render Blueprint (prod + staging)
|-- requirements.txt             # Runtime dependencies
|-- requirements-dev.txt         # Test dependencies
+-- requirements-export.txt      # PDF/Excel export dependencies
```

---

## API Overview

The application exposes endpoints across 12 route groups. All data-fetching endpoints require DHIS2 authentication. Many report endpoints serve both JSON (for API consumers) and HTMX partials (for the browser UI) depending on the `HX-Request` header.

| Route Group | Prefix | Auth | Purpose |
|---|---|---|---|
| Health | `/health/*` | No | Liveness, readiness, startup, cache stats |
| Auth | `/auth/*` | No | Login, logout, session status, refresh |
| Indicators | `/api/indicators/*` | Yes | List definitions, calculate single or all |
| Reports | `/api/reports/*` | Yes | WHO scorecards, cascades, comparisons |
| Data Quality | `/api/data-quality/*` | Yes | DQ checks, scoring, rule-based validation |
| Alerts | `/api/alerts/*` | Yes | Threshold evaluation, alert summaries |
| Trends | `/api/trends/*` | Yes | Multi-period directional analysis |
| AI Insights | `/api/insights/*` | Yes | LLM-generated narratives and recommendations |
| Exports | `/api/exports/*` | Yes | Excel and PDF report downloads |
| Org Units | `/api/org-units/*` | Yes | Hierarchy navigation, search, drill-down |
| Admin | `/admin/*` | Yes | Config validation, diagnostics |
| Pages | `/*` | Mixed | Browser-facing HTMX page routes |

See [docs/API_REFERENCE.md](docs/API_REFERENCE.md) for full endpoint documentation.

---

## Security Model

- **No local user database.** Authentication is delegated entirely to DHIS2 -- the app verifies credentials against `/api/me` on the target instance.
- **Server-side sessions.** Credentials are stored in server memory only, never in cookies or local storage. The browser receives a signed, httponly, secure session cookie that references the server-side session.
- **RBAC from DHIS2 authorities.** The app maps DHIS2 user authorities to four internal roles (viewer, analyst, data_manager, admin) and gates features accordingly.
- **CSRF protection.** State-changing requests require a session-bound CSRF token with constant-time comparison.
- **Rate limiting.** Per-session and per-IP limits for API calls, exports, AI insights, and login attempts.
- **Audit logging.** Login, logout, session expiry, rate-limit events, and sensitive operations are logged with user ID, IP, and timestamps.
- **Security headers.** CSP, X-Frame-Options (DENY), X-Content-Type-Options, Referrer-Policy, and Permissions-Policy on every response.

---

## Testing

```bash
# Unit tests (no external dependencies)
pytest tests/unit -v

# API tests (FastAPI TestClient, mocked DHIS2)
pytest tests/api -v

# Integration tests (requires live DHIS2 -- excluded by default)
pytest tests/integration -v -m integration

# All tests except integration
pytest
```

The test suite covers unit tests (calculator, cache, connector, RBAC, registry, session, alerts, DQ, supply chain, AI insights), API route tests (all 12 route groups), and integration tests with a mock DHIS2 server.

---

## Documentation

| Document | Description |
|---|---|
| [User Guide](docs/USER_GUIDE.md) | End-user walkthrough of all features |
| [API Reference](docs/API_REFERENCE.md) | Full endpoint documentation with request/response examples |
| [Developer Guide](docs/DEVELOPER_GUIDE.md) | Architecture, code conventions, contribution workflow |
| [Indicator Catalog](docs/INDICATOR_CATALOG.md) | All 30 indicators with formulas, data sources, and targets |
| [Configuration Guide](docs/CONFIGURATION.md) | YAML config files, field-by-field reference |
| [Deployment Guide](docs/DEPLOYMENT.md) | Docker, Render, health checks, operational runbook |

---

## Known Limitations

- **Single-instance only.** Sessions and caches are in-memory. Scaling to multiple workers or instances requires migrating to Redis or a database-backed session store.
- **Reporting completeness** indicator is not yet wired to HMIS dataset UIDs and will show as unavailable.
- **Population denominators** require manual configuration in `config/populations.yaml` using UBOS district projections.
- **AI insights** are optional and require an LLM API key. Without one, the system falls back to rule-based summaries.

---

## License

This project is developed for the Uganda Ministry of Health PMTCT Triple Elimination programme.
