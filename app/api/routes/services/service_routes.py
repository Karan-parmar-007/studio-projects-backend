from fastapi import APIRouter
from pydantic import BaseModel

from app.api.deps import DbDep
from app.api.routes.projects.project_catalog import find_project
from app.api.routes.services import service_switches as switches
from app.auth.sso import AdminDep
from app.core.errors import NotFoundError

router = APIRouter(prefix="/admin/services", tags=["admin-services"])


class ServiceUpdate(BaseModel):
    enabled: bool


class ServiceOut(BaseModel):
    slug: str
    enabled: bool


@router.put("/{slug}", response_model=ServiceOut)
async def set_service(slug: str, body: ServiceUpdate, user: AdminDep, db: DbDep) -> ServiceOut:
    if find_project(slug) is None:
        raise NotFoundError("That product is not in Studio")
    enabled = await switches.set_enabled(db, slug, body.enabled)
    return ServiceOut(slug=slug, enabled=enabled)
