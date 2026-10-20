# Studio Backend — Agent Context

> Read this file first, then `README.md`, before changing code here.

## What this is

The API behind studio.karanparmar.in. Products are **projects**: one folder per
product under `app/api/routes/`, following `all-project-backend`. Three cross-cutting
pieces serve every project:

1. **projects/** — the static catalog the website lists (`project_catalog.py`).
2. **api_key/** — one live key per user (`sk-studio_` + 32 random bytes, SHA-256
   stored), valid for every product. `ApiKeyDep` is the bearer dependency every
   `/v1/*` route uses.
3. **model_links/** — per user, per project rows of (role, placement, url, enabled).
   A project declares its slots and defaults; `ensure_links` creates the rows lazily.

`services/routing.py` picks which link to dial (local first, toggle must be on,
speech never falls back). `services/gateway.py` POSTs to the gateway with
`X-Flow-Token` and relays a queued `202`.

It does **not** run any model. Models live in the `studio-services` repo:
`gateway/`, one Flask service with an LLM half (Ollama) and a speech half (Tara, CUDA),
each switched on by env. The LLM VPS runs the LLM half; the PC runs both on one port.

## Layout

```
main.py                         FastAPI app, lifespan (Postgres), SSO config, main_router
karan_sso/                      VENDORED shared SSO SDK — never edit, sync from sso-backend/sdk
app/
  config.py                     Database, Auth (+ gateway defaults, FLOW_TOKEN), Cors
  api/
    main_router.py              APIRouter(prefix="/api/studio"); public / signed-in / v1 sections
    deps.py                     DbDep
    responses.py                present(), job_response()
    routes/
      health/                   health_routes.py
      projects/                 project_catalog.py, project_schemas.py, project_routes.py
      api_key/                  model.py, api_key_schemas.py, api_key_service.py,
                                api_key_auth.py (ApiKeyDep), api_key_routes.py
      model_links/              model.py, model_links_schemas.py, model_links_service.py
      downloads/                model.py, downloads_service.py, downloads_auth.py (release token),
                                downloads_routes.py (public GET + token PUT). Bytes live in Garage.
      whisperflow/              whisperflow_constants.py (slots, operations, defaults),
                                whisperflow_schemas.py, whisperflow_service.py,
                                whisperflow_routes.py (SSO), whisperflow_public_routes.py (/v1)
  auth/sso.py                   IdentityDep re-export
  core/errors.py, keys.py       DomainError family → HTTP codes; key generation
  db/base.py, session.py        Declarative Base; async engine with search_path=studio
  db/garage.py                  S3-compatible Garage client (app binaries); per-request clients
  services/routing.py, gateway.py
migrations/                     Alembic (async env, version table in the studio schema)
tests/                          conftest: anon_client / user_client (SSO identity overridden)
```

## Adding a product

1. `projects/project_catalog.py` — add the entry (slug, page, endpoints).
2. `app/api/routes/<slug>/` — `<slug>_constants.py` (SLOTS, OPERATIONS, defaults),
   `<slug>_service.py` (uses `model_links_service` + `gateway`), `<slug>_routes.py`
   (SSO, prefix `/<slug>`), `<slug>_public_routes.py` (ApiKeyDep, prefix `/v1/<slug>`).
3. Include both routers in `main_router.py`.
4. If it needs new tables: `model.py` in its folder, import it in `migrations/env.py`,
   `alembic revision --autogenerate`.

## Rules

- **Async everywhere.** Routes are HTTP-only; services own commits.
- **Domain errors, not HTTPException** in services. Handlers live in `core/errors.py`.
- **Never return a link URL in an error body** — `ModelOfflineError` strips `url`.
- **Never log tokens, API keys, or FLOW_TOKEN.**
- **No users table.** Identity is the SSO JWT via `karan_sso`. `/v1/*` is bearer
  *API key* auth (our own keys, `api_key_auth.py`), not SSO.
- **CSRF**: `karan_sso.fastapi.CSRFMiddleware`; `/health`, `/api/studio/projects`
  and `/api/studio/v1` are exempt because they never use cookies.
- **One uvicorn worker.** `gateway._JOBS` is in memory. Move it to Postgres/Redis
  before scaling workers.
- **Schema changes go through Alembic**; `create_all` is never called.
- **Minimal diffs.** Match existing structure; do not refactor unrelated code.
