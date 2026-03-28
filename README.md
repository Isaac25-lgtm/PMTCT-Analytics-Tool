<div align="center">

# 🔬 PMTCT Triple Elimination Analytics Tool

### Real-time DHIS2-powered analytics for eliminating mother-to-child transmission of HIV, Syphilis & Hepatitis B across Uganda

[![Python](https://img.shields.io/badge/Python-3.10+-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100+-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![DHIS2](https://img.shields.io/badge/DHIS2-Connected-0080FF?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHdpZHRoPSIyNCIgaGVpZ2h0PSIyNCIgdmlld0JveD0iMCAwIDI0IDI0Ij48Y2lyY2xlIGN4PSIxMiIgY3k9IjEyIiByPSIxMCIgZmlsbD0id2hpdGUiLz48L3N2Zz4=&logoColor=white)](https://dhis2.org)
[![License](https://img.shields.io/badge/License-MIT-green?style=for-the-badge)](LICENSE)
[![Deploy](https://img.shields.io/badge/Render-Deployed-46E3B7?style=for-the-badge&logo=render&logoColor=white)](https://render.com)

<br/>

**A stateless, config-driven analytics dashboard** built for the Uganda Ministry of Health to monitor PMTCT Triple Elimination performance across **146 districts** -- transforming raw DHIS2 health data into actionable intelligence for program managers, district health officers, and implementing partners.

<br/>

[Getting Started](#-quick-start) · [Features](#-key-features) · [Architecture](#-architecture) · [Documentation](#-documentation) · [Deployment](#-deployment)

---

</div>

## 🎯 The Problem

Uganda's PMTCT program generates thousands of data points weekly across hundreds of health facilities. Program managers need to:

- **Detect emerging coverage gaps** before they become crises
- **Monitor triple elimination targets** (HIV, syphilis, hepatitis B) simultaneously
- **Identify supply chain risks** that threaten service delivery
- **Track data quality** to ensure decision-making rests on reliable information

Without a purpose-built analytics layer, this means hours of manual Excel work per district, delayed response to outbreaks, and fragmented visibility across the cascade.

## 💡 The Solution

This tool connects directly to Uganda's national DHIS2 instance and delivers **automated, real-time analytics** through an intuitive web dashboard -- no local data storage, no complex setup, no manual extraction.

<div align="center">

```
+-------------------------------------------------------------+
|                     DHIS2 Instance                          |
|              (Uganda National HIS Server)                   |
+----------------------+--------------------------------------+
                       |  Secure API (httpx + connection pool)
                       v
+-------------------------------------------------------------+
|              PMTCT Analytics Engine                          |
|  +----------+ +----------+ +----------+ +--------------+   |
|  |Indicators| |Data Qual.| |  Alerts  | |  AI Insights |   |
|  |  Engine  | |  Scoring | |  System  | |   (LLM API)  |   |
|  +----------+ +----------+ +----------+ +--------------+   |
|  +----------+ +----------+ +----------+ +--------------+   |
|  |  Trends  | | Supply   | |  RBAC &  | |   Report     |   |
|  | Analysis | |  Chain   | |  Audit   | |  Generation  |   |
|  +----------+ +----------+ +----------+ +--------------+   |
+----------------------+--------------------------------------+
                       |  HTMX + Chart.js + Tailwind CSS
                       v
+-------------------------------------------------------------+
|                  Web Dashboard (Browser)                     |
|         Program Managers . DHOs . Partners . MoH            |
+-------------------------------------------------------------+
```

</div>

## ✨ Key Features

### 📊 Analytics & Monitoring
- **30 PMTCT indicators** with machine-readable formulas covering testing, treatment, and retention cascades
- **49 DHIS2 data element mappings** pre-configured for Uganda's HMIS
- **Trend analysis** with temporal visualizations for spotting coverage drift
- **Composite risk scoring** with configurable weights for multi-dimensional facility assessment

### 🔔 Alerting & Intelligence
- **Automated threshold alerts** for coverage drops, stockouts, data quality issues, and missed appointments
- **AI-powered narrative insights** via vendor-neutral LLM integration -- turns numbers into plain-language recommendations
- **Data quality scoring** that flags reporting gaps, outliers, and consistency issues before they corrupt analysis

### 🏥 Operational Tools
- **Org-unit hierarchy navigation** -- drill from national to regional to district to facility
- **Supply chain monitoring** with enriched reporting on commodity availability
- **Excel & PDF report generation** (openpyxl + WeasyPrint) for offline sharing and formal reporting
- **UBOS population denominators** for accurate coverage rate calculations

### 🔒 Enterprise-Grade Security
- **DHIS2 passthrough authentication** -- no separate credentials to manage
- **Role-based access control** (RBAC) aligned with DHIS2 user roles
- **CSRF protection, rate limiting, and audit logging** out of the box
- **Stateless by design** -- zero PHI stored locally; data lives in DHIS2

## 🏗 Architecture

The system follows three core architectural principles:

| Principle | What It Means |
|:--|:--|
| **Stateless** | All data is fetched on-demand from DHIS2. Nothing is persisted locally. The tool is a pure analytics lens over existing infrastructure. |
| **Session-Only** | Users authenticate via DHIS2 credentials and work within expiring application sessions. No user database to maintain. |
| **Config-Driven** | Indicators, mappings, thresholds, and scoring weights live in YAML files -- change program logic without touching code. |

### Tech Stack

| Layer | Technology | Purpose |
|:--|:--|:--|
| **Backend** | FastAPI, httpx, pandas, pydantic | API routing, async DHIS2 connectivity, data transformation, validation |
| **Frontend** | Jinja2, HTMX, Chart.js, Tailwind CSS | Server-rendered pages with reactive updates -- no heavy SPA framework |
| **Reports** | openpyxl, WeasyPrint | Programmatic Excel workbook and PDF generation |
| **AI** | Vendor-neutral LLM integration | Natural language insight generation from structured analytics |
| **Infra** | Docker, Render, GitHub Actions | Containerized deployment with CI/CD and health checks |

## 🚀 Quick Start

```bash
# 1. Clone the repository
git clone https://github.com/Isaac25-lgtm/PMTCT-Analytics-Tool.git
cd PMTCT-Analytics-Tool

# 2. Configure environment
cp .env.example .env
# Edit .env with your DHIS2 instance URL and credentials

# 3. Install dependencies
pip install -r requirements.txt -r requirements-dev.txt -r requirements-export.txt

# 4. Launch the application
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open `http://localhost:8000` and authenticate with your DHIS2 credentials.

### Docker

```bash
docker-compose up --build
```

## ⚙️ Configuration

All program logic is externalized into YAML configuration files under `config/`:

| File | Description |
|:--|:--|
| `mappings.yaml` | 49 data element UIDs + 5 category option combo UIDs for AN21-POS extraction |
| `indicators.yaml` | 30 indicator definitions with machine-readable formulas |
| `populations.yaml` | UBOS district population template for coverage denominators |
| `scoring.yaml` | Composite risk score weights (configurable per program priority) |
| `thresholds.yaml` | Alert thresholds for coverage, stock, data quality, and missed appointments |
| `cache.yaml` | In-memory caching rules for DHIS2 response optimization |
| `rbac.yaml` | Role-based access control mappings |
| `production.yaml` | Production environment overrides |

> **Adapt to any country:** Replace the YAML mappings with your national DHIS2 metadata UIDs to deploy this tool for any DHIS2-based health information system.

## 🌍 Deployment

The tool is production-ready for cloud deployment on **Render** with included configuration:

- `render.yaml` -- Render blueprint for one-click deployment
- `Dockerfile` -- Multi-stage container build
- `docker-compose.yml` -- Local development orchestration
- `.github/workflows/` -- CI pipeline with automated testing
- `scripts/healthcheck.sh` -- Container health monitoring
- `scripts/start.sh` -- Production startup with `PORT` environment variable support

> Render injects `PORT` automatically. The startup script honors it and falls back to `8000` locally. Keep secrets (`SECRET_KEY`, `LLM_API_KEY`) in environment variables -- never in the repository.

See **[docs/DEPLOYMENT.md](docs/DEPLOYMENT.md)** for complete operational guidance.

## 📚 Documentation

| Document | Description |
|:--|:--|
| [User Guide](docs/USER_GUIDE.md) | End-user walkthrough for navigating the dashboard |
| [API Reference](docs/API_REFERENCE.md) | REST endpoint specifications for integrators |
| [Developer Guide](docs/DEVELOPER_GUIDE.md) | Setup, contribution workflow, and codebase orientation |
| [Indicator Catalog](docs/INDICATOR_CATALOG.md) | Full definitions of all 30 PMTCT indicators |
| [Configuration Guide](docs/CONFIGURATION.md) | YAML schema documentation and customization instructions |
| [Deployment Guide](docs/DEPLOYMENT.md) | Docker, Render, health checks, and operations |

## 📁 Project Structure

```
PMTCT-Analytics-Tool/
|-- app/
|   |-- api/              # FastAPI route handlers
|   |-- auth/             # DHIS2 authentication & session management
|   |-- connectors/       # DHIS2 API client (pooled, async)
|   |-- core/             # Shared config, caching, middleware
|   |-- indicators/       # Indicator calculation engine
|   |-- reports/          # Excel & PDF report generation
|   |-- services/         # Business logic (alerts, DQ, trends, AI)
|   |-- templates/        # Jinja2 HTML templates + HTMX partials
|   +-- main.py           # Application entrypoint
|-- config/               # YAML-driven program configuration
|-- docs/                 # End-user & developer documentation
|-- scripts/              # Startup & health check scripts
|-- static/               # CSS, JS, and static assets
|-- tests/                # pytest test suite
|-- .github/workflows/    # CI/CD pipeline
|-- Dockerfile            # Container build
|-- docker-compose.yml    # Local orchestration
|-- render.yaml           # Render deployment blueprint
+-- requirements*.txt     # Dependency manifests
```

## 🤝 Contributing

Contributions are welcome. Please read the [Developer Guide](docs/DEVELOPER_GUIDE.md) for setup instructions and coding conventions before submitting a pull request.

```bash
# Run tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html
```

## 📜 License

This project is open source under the [MIT License](LICENSE).

---

<div align="center">

**Built for Uganda's health data ecosystem**

*Transforming DHIS2 data into decisions that save lives*

</div>
