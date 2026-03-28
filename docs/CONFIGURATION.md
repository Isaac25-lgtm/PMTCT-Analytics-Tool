# Configuration Reference

## Overview

Configuration is split between environment variables and YAML files under `config/`.

Guiding rules:

- secrets belong in environment variables
- business rules, mappings, and thresholds belong in YAML
- the app does not persist mutable configuration at runtime

## Environment variables

Core settings in `app/core/config.py`:

### Application
- `APP_NAME`
- `APP_VERSION`
- `APP_ENV`
- `APP_DEBUG`
- `PORT` or `APP_PORT`
- `APP_HOST`
- `SECRET_KEY`

### DHIS2 defaults
- `DHIS2_BASE_URL`
- `DHIS2_API_VERSION`
- `SESSION_TIMEOUT_MINUTES`
- `DHIS2_TIMEOUT_SECONDS`
- `DHIS2_MAX_RETRIES`

### Logging
- `LOG_LEVEL`
- `LOG_FORMAT`

### LLM
- `LLM_PROVIDER`
- `LLM_API_KEY`
- `LLM_MODEL`
- `LLM_BASE_URL`
- `LLM_AZURE_ENDPOINT`
- `LLM_MAX_TOKENS`
- `LLM_TEMPERATURE`
- `LLM_TIMEOUT` or `LLM_TIMEOUT_SECONDS`
- `LLM_ENABLED`
- `LLM_FALLBACK_ENABLED`
- `LLM_MAX_CONTENT_LENGTH`

### Security and audit
- `AUDIT_ENABLED`
- `AUDIT_LOG_FILE`
- `RATE_LIMIT_ENABLED`
- `CSRF_ENABLED`

### Cache
- `CACHE_ENABLED`
- `CACHE_MAX_SIZE`
- `CACHE_DEFAULT_TTL`
- `CACHE_METADATA_TTL`
- `CACHE_HIERARCHY_TTL`
- `CACHE_AGGREGATE_TTL`
- `CACHE_INDICATOR_TTL`
- `CACHE_TREND_TTL`
- `CACHE_INSIGHT_TTL`
- `CACHE_DATA_QUALITY_TTL`
- `CACHE_ALERT_TTL`

### HTTP pool
- `HTTP_MAX_CONNECTIONS`
- `HTTP_MAX_KEEPALIVE`
- `HTTP_KEEPALIVE_EXPIRY`
- `HTTP_CONNECT_TIMEOUT`
- `HTTP_READ_TIMEOUT`
- `HTTP_WRITE_TIMEOUT`
- `HTTP_POOL_TIMEOUT`

## YAML files

### `config/indicators.yaml`
- indicator registry
- formulas, result types, periodicity, targets

### `config/mappings.yaml`
- DHIS2 data element mappings
- category option combo definitions

### `config/commodities.yaml`
- supply tracer commodities
- supply indicator links and mapping codes
- mapping status for unmapped items

### `config/alert_thresholds.yaml`
- alert definitions for indicator, supply, and system thresholds

### `config/dq_rules.yaml`
- data-quality rules
- cascade consistency pairs
- service-to-supply reconciliation pairs

### `config/rbac.yaml`
- role mapping from DHIS2 authorities
- permission matrix
- rate-limit defaults
- audit enablement

### `config/cache.yaml`
- cache enablement
- shared TTL defaults
- connection-pool defaults

### `config/thresholds.yaml`
- lightweight threshold groups used by some services and UI summaries

### `config/populations.yaml`
- expected pregnancy denominators
- district, regional, and national population scaffolding

### `config/scoring.yaml`
- composite risk score weights

### `config/org_hierarchy.yaml`
- org-unit level names
- drill-down and aggregation behavior

### `config/production.yaml`
- production override reference for deployment

## Validation

Run:

```bash
python scripts/validate_config.py --verbose
```

The validator checks:

- file presence
- YAML parseability
- live schema expectations for core files
- obvious contract mismatches such as missing indicator IDs or invalid commodity mapping states

## Deployment notes

- `.env.example` is the starting point for local development.
- production should inject secrets through the hosting platform
- the application remains single-instance for the MVP because session and cache state are process-local
