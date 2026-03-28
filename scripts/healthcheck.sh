#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8000}"
TIMEOUT="${HEALTHCHECK_TIMEOUT:-5}"

response="$(curl -sf --max-time "${TIMEOUT}" "http://localhost:${PORT}/health/live")"

if ! echo "${response}" | grep -q '"status":"healthy"'; then
  echo "Health check failed: unexpected response ${response}"
  exit 1
fi

exit 0
