# =============================================================================
# PMTCT Triple Elimination Tool - Production Dockerfile
# =============================================================================

FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libcairo2-dev \
    libffi-dev \
    libgdk-pixbuf-2.0-dev \
    libpango1.0-dev \
    pkg-config \
    shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements-dev.txt requirements-export.txt ./

RUN pip install --no-cache-dir --upgrade pip \
    && pip wheel --no-cache-dir --wheel-dir /wheels \
        -r requirements.txt \
        -r requirements-export.txt


FROM python:3.11-slim AS production

LABEL org.opencontainers.image.title="PMTCT Triple Elimination Tool"
LABEL org.opencontainers.image.description="DHIS2-connected PMTCT analytics tool for Uganda MoH"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    APP_ENV=production \
    PORT=8000 \
    WEB_CONCURRENCY=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    curl \
    libcairo2 \
    libffi8 \
    libgdk-pixbuf-2.0-0 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    shared-mime-info \
    tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --shell /bin/bash appuser

COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

COPY --chown=appuser:appuser app/ ./app/
COPY --chown=appuser:appuser config/ ./config/
COPY --chown=appuser:appuser static/ ./static/
COPY --chown=appuser:appuser scripts/ ./scripts/
COPY --chown=appuser:appuser README.md ./

RUN chmod +x ./scripts/start.sh ./scripts/healthcheck.sh

USER appuser

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD ["/app/scripts/healthcheck.sh"]

EXPOSE 8000

ENTRYPOINT ["/usr/bin/tini", "--"]
CMD ["/app/scripts/start.sh"]
