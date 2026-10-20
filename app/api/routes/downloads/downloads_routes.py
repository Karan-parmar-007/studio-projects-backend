"""App binaries. Public GET to list and download; token-gated PUT to upload (CI)."""

from __future__ import annotations

from fastapi import APIRouter, Header, Request
from fastapi.responses import Response

from app.api.deps import DbDep, GarageClientDep
from app.api.routes.downloads import downloads_service as service
from app.api.routes.downloads.downloads_auth import ReleaseTokenDep
from app.api.routes.downloads.downloads_schemas import DownloadOut
from app.auth.sso import AdminDep

# Public: list + stream, plus cookie/SSO admin upload and delete
router = APIRouter(prefix="/downloads", tags=["downloads"])
# CI upload: token-gated
admin_router = APIRouter(prefix="/admin/downloads", tags=["downloads-admin"])


@router.get("", response_model=list[DownloadOut])
async def list_downloads(db: DbDep) -> list:
    return await service.list_downloads(db)


@router.get("/{product}/{platform}")
async def download(product: str, platform: str, db: DbDep, garage: GarageClientDep) -> Response:
    row, stored = await service.fetch(db, garage, product, platform)
    return Response(
        content=stored.content,
        media_type=row.content_type or "application/octet-stream",
        headers={
            "Content-Disposition": f'attachment; filename="{row.filename}"',
            "Content-Length": str(stored.size),
            "Cache-Control": "public, max-age=300",
        },
    )


@router.put("/{product}/{platform}", response_model=DownloadOut, status_code=201)
async def upload_admin(
    product: str,
    platform: str,
    request: Request,
    db: DbDep,
    garage: GarageClientDep,
    user: AdminDep,
    x_filename: str = Header(..., description="The file name to serve, e.g. StudioFlow.exe"),
    x_version: str = Header(default=""),
    content_type: str = Header(default="application/octet-stream"),
):
    body = await request.body()
    return await service.store(
        db,
        garage,
        product,
        platform,
        filename=x_filename,
        content_type=content_type,
        version=x_version,
        content=body,
    )


@router.delete("/{product}/{platform}", status_code=204)
async def delete_admin(
    product: str,
    platform: str,
    db: DbDep,
    garage: GarageClientDep,
    user: AdminDep,
):
    await service.delete_download(db, garage, product, platform)
    return Response(status_code=204)


@admin_router.put("/{product}/{platform}", response_model=DownloadOut, status_code=201)
async def upload(
    product: str,
    platform: str,
    request: Request,
    db: DbDep,
    garage: GarageClientDep,
    _token: ReleaseTokenDep,
    x_filename: str = Header(..., description="The file name to serve, e.g. StudioFlow.exe"),
    x_version: str = Header(default=""),
    content_type: str = Header(default="application/octet-stream"),
):
    body = await request.body()
    return await service.store(
        db,
        garage,
        product,
        platform,
        filename=x_filename,
        content_type=content_type,
        version=x_version,
        content=body,
    )


@admin_router.delete("/{product}/{platform}", status_code=204)
async def delete_token(
    product: str,
    platform: str,
    db: DbDep,
    garage: GarageClientDep,
    _token: ReleaseTokenDep,
):
    await service.delete_download(db, garage, product, platform)
    return Response(status_code=204)
