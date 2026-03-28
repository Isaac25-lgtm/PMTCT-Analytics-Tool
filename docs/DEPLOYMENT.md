# Deployment Guide

This application is deployed as a **single-instance, stateless Render web service**. All sessions, caches, alert acknowledgement state, and pooled connections live only in memory and are lost on restart. That is intentional for the MVP.

## Deployment model

- Platform: Render Web Service
- Runtime: Docker
- Process model: **1 Uvicorn worker**
- TLS: Render-managed
- State: in-memory only
- Horizontal scaling: not supported in MVP

## Why one worker matters

The live app now uses:

- in-memory session storage
- in-memory app and session caches
- per-process alert acknowledgement state
- a singleton shared `httpx.AsyncClient`

Those are all process-local. Running more than one worker would split state across workers and make behavior inconsistent. Keep `WEB_CONCURRENCY=1`.

## Required environment variables

Set these in Render:

| Variable | Required | Notes |
| --- | --- | --- |
| `APP_ENV` | Yes | `production` on prod, `staging` on staging |
| `SECRET_KEY` | Yes | Generate a long random value |
| `DHIS2_BASE_URL` | Yes | Base DHIS2 URL without trailing `/api` |
| `LOG_LEVEL` | Yes | Usually `INFO` in production |
| `LOG_FORMAT` | Yes | `json` in production |
| `SESSION_TIMEOUT_MINUTES` | Yes | Default `60` |
| `CACHE_ENABLED` | Yes | Usually `true` |
| `RATE_LIMIT_ENABLED` | Yes | Usually `true` |
| `CSRF_ENABLED` | Yes | Usually `true` |
| `AUDIT_ENABLED` | Yes | Usually `true` |
| `LLM_PROVIDER` | No | Example: `gemini`, `openai`, `azure-openai`, `anthropic` |
| `LLM_API_KEY` | No | Needed only when AI insights are enabled |
| `LLM_MODEL` | No | Provider-specific model name, for example `gemini-3-flash-preview` |
| `LLM_BASE_URL` | No | Optional OpenAI-compatible endpoint override; Gemini defaults automatically |
| `LLM_AZURE_ENDPOINT` | No | For Azure OpenAI |

## Health endpoints

Render should use:

- Liveness: `/health/live`
- Readiness: `/health/ready`

Additional operational endpoints:

- `/health`
- `/health/startup`
- `/health/cache`
- `/health/stats`

## Local Docker workflow

```bash
cp .env.example .env
docker compose up --build
```

The compose file runs the app with:

- `APP_ENV=development`
- `LOG_FORMAT=console`
- `CSRF_ENABLED=false`
- `RATE_LIMIT_ENABLED=false`
- source mounts for live code reload

## Render deployment flow

1. Connect the repository to Render using `render.yaml`
2. Set the required secrets in the Render dashboard
3. Deploy the `main` branch for production
4. The included staging service also targets `main` by default
5. If you want branch-based pre-production validation later, create a
   `staging` branch in GitHub and update `render.yaml`

## Operational notes

- Redeploys clear all sessions and caches
- App shutdown closes the shared `httpx` client and clears caches
- Session destroy clears the session cache
- Request logs include an `X-Request-ID` correlation value
- Production logs should be JSON-formatted

## Troubleshooting

### Health check fails

Check:

- `DHIS2_BASE_URL` is set correctly
- the indicator registry loaded during startup
- `/health/ready` for detailed component checks

### Users are logged out after deploy

Expected in MVP. Session state is in memory only.

### Cache looks ineffective

Check `/health/cache` for hit rate, evictions, and size.

### AI insights unavailable

Verify:

- `LLM_PROVIDER`
- `LLM_API_KEY`
- optional `LLM_MODEL`
- for Gemini 3 Flash, use `LLM_PROVIDER=gemini` and `LLM_MODEL=gemini-3-flash-preview`

## Security reminders

- Never commit `.env`
- Keep `SECRET_KEY` and API keys only in Render secrets
- Keep `WEB_CONCURRENCY=1`
- Use Render-managed HTTPS only
