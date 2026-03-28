# PMTCT Triple Elimination Analytics Tool

Stateless, DHIS2-connected analytics dashboard for Uganda Ministry of Health PMTCT Triple Elimination monitoring across HIV, syphilis, and hepatitis B.

This repository now includes the specification-complete MVP through the final production-readiness prompt, including:

- DHIS2 authentication and session handling
- indicator calculation and report routes
- HTMX frontend pages and partials
- data quality, alerts, trends, and AI insights
- org-unit hierarchy navigation
- RBAC, CSRF, rate limiting, and audit logging
- in-memory caching and pooled DHIS2 connections
- enriched supply-chain reporting
- container, Render, CI, and deployment assets
- end-user, API, developer, indicator, and configuration documentation
- admin diagnostics and config-validation utilities

## Architecture

- Stateless: data is fetched on demand from DHIS2 and not stored locally.
- Session-only: users authenticate with DHIS2 and work within an expiring application session.
- Config-driven: indicators, mappings, thresholds, and scoring live in YAML files under `config/`.

## Tech Stack

| Layer | Technology |
| --- | --- |
| Backend | FastAPI, httpx, pandas, pydantic |
| Frontend | Jinja2, HTMX, Chart.js, Tailwind CSS |
| Reports | openpyxl, WeasyPrint |
| AI | Vendor-neutral LLM integration |

## Quick Start

```bash
cp .env.example .env
pip install -r requirements.txt -r requirements-dev.txt -r requirements-export.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

## Configuration

All DHIS2 metadata mappings and indicator definitions are kept in `config/`:

- `mappings.yaml`: 49 data element UIDs plus 5 category option combo UIDs for AN21-POS extraction
- `indicators.yaml`: 30 indicator definitions with machine-readable formulas
- `populations.yaml`: UBOS district population template for coverage denominators
- `scoring.yaml`: configurable composite risk score weights
- `thresholds.yaml`: alert thresholds for coverage, stock, data quality, and missed appointments

## Deployment

- See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for Docker, Render, health checks, and operational guidance.
- Render injects a `PORT` environment variable automatically. The startup script honors it and keeps the app on a single worker for MVP consistency.
- Keep secrets such as `SECRET_KEY` and `LLM_API_KEY` in environment variables, not in the repository.

## Documentation

- [docs/USER_GUIDE.md](docs/USER_GUIDE.md)
- [docs/API_REFERENCE.md](docs/API_REFERENCE.md)
- [docs/DEVELOPER_GUIDE.md](docs/DEVELOPER_GUIDE.md)
- [docs/INDICATOR_CATALOG.md](docs/INDICATOR_CATALOG.md)
- [docs/CONFIGURATION.md](docs/CONFIGURATION.md)
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)

## Render Deployment Notes

- Render injects a `PORT` environment variable automatically. The Docker command honors `PORT` and falls back to `8000` locally.
- Keep secrets such as `SECRET_KEY` and `LLM_API_KEY` in Render environment variables, not in the repository.
- `TEMP_DIR` defaults to `/tmp/pmtct_reports`, which is suitable for Linux-based Render containers.

## Project Structure

```text
pmtct_elimination/
|-- app/
|   |-- api/
|   |-- auth/
|   |-- connectors/
|   |-- core/
|   |-- indicators/
|   |-- reports/
|   |-- services/
|   |-- templates/
|   `-- main.py
|-- config/
|   |-- cache.yaml
|   |-- indicators.yaml
|   |-- production.yaml
|   `-- rbac.yaml
|-- docs/
|   `-- DEPLOYMENT.md
|-- scripts/
|   |-- healthcheck.sh
|   `-- start.sh
|-- static/
|-- tests/
|-- .env.example
|-- docker-compose.yml
|-- render.yaml
|-- Dockerfile
`-- README.md
```

This tree is intentionally high-level rather than exhaustive. The quickest way to inspect the current repo shape is `rg --files app config tests`.
