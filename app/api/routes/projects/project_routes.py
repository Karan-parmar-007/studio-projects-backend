from fastapi import APIRouter

from app.api.routes.projects.project_catalog import PROJECTS, find_project
from app.api.routes.projects.project_schemas import ProjectOut
from app.core.errors import NotFoundError

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectOut])
def list_projects() -> list[dict]:
    return PROJECTS


@router.get("/{slug}", response_model=ProjectOut)
def one_project(slug: str) -> dict:
    found = find_project(slug)
    if found is None:
        raise NotFoundError("That product is not in Studio")
    return found
