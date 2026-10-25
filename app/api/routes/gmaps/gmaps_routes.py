"""Maps Scraper on the website. SSO cookie auth, no API key."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.api.deps import DbDep
from app.api.routes.gmaps import gmaps_service as service
from app.api.routes.gmaps.gmaps_schemas import PlacesOut, QuotaOut, RunIn
from app.auth.sso import IdentityDep

router = APIRouter(prefix="/gmaps", tags=["gmaps"])

NDJSON_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def session_factory(request: Request) -> Any:
    """The app's Postgres manager, for settling a run after the request has ended."""
    return getattr(request.app.state, "postgres_session", None)


def stream_response(stream) -> StreamingResponse:
    return StreamingResponse(stream, media_type="application/x-ndjson", headers=NDJSON_HEADERS)


@router.get("/quota", response_model=QuotaOut)
async def quota(user: IdentityDep, db: DbDep):
    return await service.quota(db, user.user_id)


@router.get("/locations/countries", response_model=PlacesOut)
async def countries(user: IdentityDep):
    return PlacesOut(items=list(service.countries()))


@router.get("/locations/states", response_model=PlacesOut)
async def states(user: IdentityDep):
    return PlacesOut(items=list(service.states()))


@router.get("/locations/cities", response_model=PlacesOut)
async def cities(user: IdentityDep, state: list[str] | None = Query(default=None)):
    return PlacesOut(items=list(service.cities(state)))


@router.post("/runs/stream")
async def run(body: RunIn, request: Request, user: IdentityDep, db: DbDep):
    return stream_response(await service.start_run(db, session_factory(request), user.user_id, body))
