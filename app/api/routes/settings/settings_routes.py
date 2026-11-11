"""Admin: read and change a product's settings (who may use it, where, limits)."""

from typing import Any

from fastapi import APIRouter

from app.api.deps import DbDep
from app.api.routes.settings import settings_service as service
from app.auth.sso import AdminDep
from app.core.errors import NotFoundError, ValidationError

router = APIRouter(prefix="/admin/services", tags=["admin-services"])


def _model(slug: str):
    model = service.MODELS.get(slug)
    if model is None:
        raise NotFoundError("That product has no settings")
    return model


@router.get("/{slug}/settings")
async def read_settings(slug: str, user: AdminDep, db: DbDep) -> dict[str, Any]:
    _model(slug)
    return (await service.load(db, slug)).model_dump()


@router.put("/{slug}/settings")
async def write_settings(slug: str, body: dict[str, Any], user: AdminDep, db: DbDep) -> dict[str, Any]:
    model = _model(slug)
    try:
        values = model.model_validate(body)
    except Exception as exc:  # pydantic.ValidationError
        raise ValidationError(f"Those settings are not valid: {exc}") from exc
    return (await service.save(db, slug, values)).model_dump()
