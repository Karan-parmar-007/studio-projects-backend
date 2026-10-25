"""Maps Scraper for anyone with a Studio API key. Same daily quota as the website."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request

from app.api.deps import DbDep
from app.api.routes.api_key.api_key_auth import ApiKeyDep
from app.api.routes.gmaps import gmaps_service as service
from app.api.routes.gmaps.gmaps_routes import session_factory, stream_response
from app.api.routes.gmaps.gmaps_schemas import PlacesOut, QuotaOut, RunIn

router = APIRouter(prefix="/v1/gmaps", tags=["gmaps-public"])


@router.get("/quota", response_model=QuotaOut)
async def quota(key: ApiKeyDep, db: DbDep):
    return await service.quota(db, key.owner_id)


@router.get("/locations/countries", response_model=PlacesOut)
async def countries(key: ApiKeyDep):
    return PlacesOut(items=list(service.countries()))


@router.get("/locations/states", response_model=PlacesOut)
async def states(key: ApiKeyDep):
    return PlacesOut(items=list(service.states()))


@router.get("/locations/cities", response_model=PlacesOut)
async def cities(key: ApiKeyDep, state: list[str] | None = Query(default=None)):
    return PlacesOut(items=list(service.cities(state)))


@router.post("/runs/stream")
async def run(body: RunIn, request: Request, key: ApiKeyDep, db: DbDep):
    return stream_response(await service.start_run(db, session_factory(request), key.owner_id, body))
