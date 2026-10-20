# Studio API

FastAPI backend for [studio.karanparmar.in](https://studio.karanparmar.in). Products
are **projects**, one folder each under `app/api/routes/` (like `all-project-backend`).
A user has **one API key** that works for every product, and each product keeps its
own **model links** (where its models live for that user). First product: WhisperFlow.

## Quick start

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # fill POSTGRES_*, SSO_*, FLOW_TOKEN
alembic upgrade head
uvicorn main:app --reload --port 8001
```

Verify: `curl http://localhost:8001/health`

## Routes

| Route | Auth | Purpose |
|---|---|---|
| `GET /api/studio/projects[/{slug}]` | none | product catalog (static, in code) |
| `GET /api/studio/downloads[/{product}/{platform}]` | none | list app binaries / stream one (from Garage) |
| `PUT /api/studio/admin/downloads/{product}/{platform}` | release token | CI uploads a new binary to Garage |
| `GET/POST/DELETE /api/studio/keys` | SSO cookie + CSRF | the user's one key; POST rotates and returns the secret once |
| `GET/PUT /api/studio/whisperflow/links[/{id}]` | SSO cookie + CSRF | WhisperFlow model links for this user |
| `POST /api/studio/whisperflow/transcribe|format`, `GET .../jobs/{id}` | SSO cookie + CSRF | the free playground on the website |
| `POST /api/studio/v1/whisperflow/transcribe|format`, `GET .../jobs/{id}` | `Authorization: Bearer sk-studio_...` | desktop app, Android keyboard, your own code |

A busy gateway answers `202 {status: queued, job_id, poll}`; poll until `status` is
`done` or `error`.

## How a request is routed

`app/services/routing.py` picks the link. The local link wins when its toggle is
on and a URL is saved. If the local LLM link fails, the remote (VPS) link is tried.
Speech never falls back to the server. Every gateway call carries
`X-Flow-Token: FLOW_TOKEN`.

## Ports (local dev)

| Service | Port |
|---|---|
| This API | 8001 |
| SSO API | 8002 |
| Studio frontend | 5174 |
| Speech gateway (PC) | 8003 |
| LLM gateway (PC) | 8004 |
| Postgres | 5433 |

## Migrations

```bash
alembic revision --autogenerate -m "0002_short_description"
alembic upgrade head
```

`alembic_version` lives in the `studio` schema (`version_table_schema`), so it
never collides with the other apps sharing the database.

## Tests

```bash
pytest
```

No database is needed; gateway calls are faked.

## Deploy

Push to `master` → tests → GHCR image `ghcr.io/karan-parmar-007/studio-backend` →
`docker compose up` of `studio-backend` only, in `/srv/docker/apps/studio` on the
VPS. Compose file and `.env.example` live in `new_deployment/studio`.

## App binaries

Desktop and Android installers are stored in **Garage** (bucket `studio-media`), not in any repo. The `studio-frontend` release workflow builds them and `PUT`s them to `/api/studio/admin/downloads/...` with `STUDIO_RELEASE_TOKEN`; the public `GET /api/studio/downloads/...` streams them. Set `GARAGE_*` and `STUDIO_RELEASE_TOKEN` in `.env` (see `.env.example`).
