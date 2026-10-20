"""Downloads: public list/stream, token-gated upload. Garage is faked."""

import uuid
from datetime import UTC, datetime

import pytest
from botocore.client import BaseClient  # noqa: F401 (type only)

import main
from app.api import deps
from app.api.routes.downloads import downloads_routes, downloads_service
from app.api.routes.downloads.model import Download
from app.db import garage
from karan_sso import SsoIdentity
from karan_sso.fastapi import get_identity


def _row(product="whisperflow", platform="windows") -> Download:
    return Download(
        id=uuid.uuid4(),
        product=product,
        platform=platform,
        filename="StudioFlow.exe",
        content_type="application/vnd.microsoft.portable-executable",
        size=5,
        version="1.0",
        object_key=f"studio/downloads/{product}/{platform}/StudioFlow.exe",
        updated_at=datetime.now(UTC),
    )


async def _garage():
    yield object()


def test_list_is_public(anon_client, monkeypatch):
    async def fake_list(db):
        return [_row()]

    monkeypatch.setattr(downloads_routes.service, "list_downloads", fake_list)
    response = anon_client.get("/api/studio/downloads")
    assert response.status_code == 200
    assert response.json()[0]["platform"] == "windows"
    assert "object_key" not in response.json()[0]


def test_download_streams_the_binary(anon_client, monkeypatch):
    main.app.dependency_overrides[deps.get_garage] = _garage

    async def fake_fetch(db, client, product, platform):
        return _row(product, platform), garage.StoredFile(
            key="k", content=b"MZbin", content_type="application/octet-stream", size=5
        )

    monkeypatch.setattr(downloads_routes.service, "fetch", fake_fetch)
    response = anon_client.get("/api/studio/downloads/whisperflow/windows")
    assert response.status_code == 200
    assert response.content == b"MZbin"
    assert 'filename="StudioFlow.exe"' in response.headers["content-disposition"]


def test_upload_requires_the_release_token(anon_client, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config.auth_settings, "STUDIO_RELEASE_TOKEN", "topsecret")
    main.app.dependency_overrides[deps.get_garage] = _garage
    # no token -> 401
    bad = anon_client.put(
        "/api/studio/admin/downloads/whisperflow/windows",
        content=b"MZ",
        headers={"X-Filename": "StudioFlow.exe"},
    )
    assert bad.status_code == 401


def test_upload_stores_and_returns_metadata(anon_client, monkeypatch):
    import app.config as config

    monkeypatch.setattr(config.auth_settings, "STUDIO_RELEASE_TOKEN", "topsecret")
    main.app.dependency_overrides[deps.get_garage] = _garage
    seen = {}

    async def fake_store(db, client, product, platform, *, filename, content_type, version, content):
        seen.update(product=product, platform=platform, filename=filename, version=version, content=content)
        row = _row(product, platform)
        row.filename = filename
        row.version = version
        return row

    monkeypatch.setattr(downloads_routes.service, "store", fake_store)
    response = anon_client.put(
        "/api/studio/admin/downloads/whisperflow/macos",
        content=b"appzip",
        headers={
            "Authorization": "Bearer topsecret",
            "X-Filename": "StudioFlow-macos.zip",
            "X-Version": "1.2.0",
            "Content-Type": "application/zip",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["version"] == "1.2.0"
    assert seen["platform"] == "macos" and seen["content"] == b"appzip"


@pytest.mark.asyncio
async def test_service_rejects_unknown_platform():
    from app.core.errors import ValidationError

    with pytest.raises(ValidationError):
        await downloads_service.metadata(object(), "whisperflow", "blackberry")  # type: ignore[arg-type]


def test_admin_upload_and_delete(anon_client, user_client, admin_client, monkeypatch):
    main.app.dependency_overrides[deps.get_garage] = _garage

    # Unauthenticated cannot upload or delete
    main.app.dependency_overrides.pop(get_identity, None)
    assert anon_client.put("/api/studio/downloads/whisperflow/windows", content=b"123").status_code in (401, 403)

    # Non-admin cannot upload or delete
    main.app.dependency_overrides[get_identity] = lambda: SsoIdentity(user_id="user-1", email="u@example.com")
    assert user_client.put("/api/studio/downloads/whisperflow/windows", content=b"123").status_code == 403
    assert user_client.delete("/api/studio/downloads/whisperflow/windows").status_code == 403

    # Admin can upload and delete
    main.app.dependency_overrides[get_identity] = lambda: SsoIdentity(
        user_id="admin-1", email="admin@example.com", role="owner"
    )

    async def fake_store(db, client, product, platform, *, filename, content_type, version, content):
        row = _row(product, platform)
        row.filename = filename
        row.version = version
        return row

    async def fake_delete(db, client, product, platform):
        return None

    monkeypatch.setattr(downloads_routes.service, "store", fake_store)
    monkeypatch.setattr(downloads_routes.service, "delete_download", fake_delete)

    res = admin_client.put(
        "/api/studio/downloads/whisperflow/windows",
        content=b"testexe",
        headers={"X-Filename": "StudioFlow.exe", "X-Version": "1.0.1"},
    )
    assert res.status_code == 201
    assert res.json()["filename"] == "StudioFlow.exe"

    del_res = admin_client.delete("/api/studio/downloads/whisperflow/windows")
    assert del_res.status_code == 204



def test_cookie_admin_upload_needs_the_csrf_token(anon_client):
    """Admin upload/delete use the SSO cookie, so they must not be CSRF-exempt."""
    anon_client.cookies.set("access_token", "x")
    anon_client.cookies.set("csrf_token", "abc")
    no_header = anon_client.delete("/api/studio/downloads/whisperflow/windows")
    assert no_header.status_code == 403
    assert "CSRF" in no_header.json()["detail"]


def test_public_download_reads_need_no_csrf(anon_client, monkeypatch):
    async def fake_list(db):
        return []

    monkeypatch.setattr(downloads_routes.service, "list_downloads", fake_list)
    anon_client.cookies.set("access_token", "x")
    assert anon_client.get("/api/studio/downloads").status_code == 200
