#!/usr/bin/env bash
set -euo pipefail

echo "=== PMTCT Triple Elimination Tool ==="
echo "Environment: ${APP_ENV:-production}"
echo "Port: ${PORT:-8000}"

export PORT="${PORT:-8000}"
export LOG_LEVEL="${LOG_LEVEL:-INFO}"
export WEB_CONCURRENCY="${WEB_CONCURRENCY:-1}"
export KEEP_ALIVE="${KEEP_ALIVE:-5}"
export GRACEFUL_TIMEOUT="${GRACEFUL_TIMEOUT:-30}"

if [[ "${WEB_CONCURRENCY}" != "1" ]]; then
  echo "WARNING: forcing WEB_CONCURRENCY=1 because sessions, caches, and alert state are process-local in MVP"
  export WEB_CONCURRENCY="1"
fi

if [[ -z "${DHIS2_BASE_URL:-}" ]]; then
  echo "WARNING: DHIS2_BASE_URL is not set"
fi

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT}" \
  --workers "${WEB_CONCURRENCY}" \
  --loop uvloop \
  --http httptools \
  --proxy-headers \
  --forwarded-allow-ips="*" \
  --timeout-keep-alive "${KEEP_ALIVE}" \
  --timeout-graceful-shutdown "${GRACEFUL_TIMEOUT}" \
  --access-log \
  --log-level "$(echo "${LOG_LEVEL}" | tr '[:upper:]' '[:lower:]')"
