from fastapi import APIRouter

from app.api.deps import DbDep
from app.api.routes.api_key import api_key_service
from app.api.routes.api_key.api_key_schemas import KeyCreate, KeyCreated, KeyOut
from app.api.routes.api_key.model import ApiKey
from app.auth.sso import IdentityDep

router = APIRouter(prefix="/keys", tags=["api-key"])


def _out(row: ApiKey) -> KeyOut:
    return KeyOut(
        id=row.id,
        name=row.name,
        prefix=row.prefix,
        created_at=row.created_at,
        last_used_at=row.last_used_at,
    )


@router.get("", response_model=KeyOut | None)
async def my_key(user: IdentityDep, db: DbDep) -> KeyOut | None:
    row = await api_key_service.current_key(db, user.user_id)
    return _out(row) if row else None


@router.post("", response_model=KeyCreated, status_code=201)
async def create_or_rotate(body: KeyCreate, user: IdentityDep, db: DbDep) -> KeyCreated:
    row, secret = await api_key_service.rotate_key(db, user.user_id, body.name)
    return KeyCreated(**_out(row).model_dump(), secret=secret)


@router.delete("", status_code=204)
async def revoke(user: IdentityDep, db: DbDep) -> None:
    await api_key_service.revoke_key(db, user.user_id)
