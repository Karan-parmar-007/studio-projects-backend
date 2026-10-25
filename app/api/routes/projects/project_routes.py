from fastapi import APIRouter

from app.api.deps import DbDep
from app.api.routes.projects.project_catalog import PROJECTS, find_project
from app.api.routes.projects.project_schemas import ProjectOut
from app.api.routes.services import service_switches
from app.core.errors import NotFoundError

router = APIRouter(prefix="/projects", tags=["projects"])


def _with_flag(item: dict, flags: dict[str, bool]) -> dict:
    return {**item, "enabled": flags.get(item["slug"], True)}


@router.get("", response_model=list[ProjectOut])
async def list_projects(db: DbDep) -> list[dict]:
    flags = await service_switches.enabled_map(db)
    return [_with_flag(item, flags) for item in PROJECTS]


@router.get("/{slug}", response_model=ProjectOut)
async def one_project(slug: str, db: DbDep) -> dict:
    found = find_project(slug)
    if found is None:
        raise NotFoundError("That product is not in Studio")
    flags = await service_switches.enabled_map(db)
    return _with_flag(found, flags)
