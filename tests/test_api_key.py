import uuid
from datetime import UTC, datetime

from app.api.routes.api_key import api_key_routes, api_key_service
from app.api.routes.api_key.model import ApiKey
from app.api.routes.whisperflow import whisperflow_public_routes


def _key(owner="user-1") -> ApiKey:
    return ApiKey(
        id=uuid.uuid4(), owner_id=owner, name="Phone", prefix="sk-studio_abc", key_hash="h",
        revoked=False, created_at=datetime.now(UTC), last_used_at=None,
    )


def test_user_sees_their_one_key(user_client, monkeypatch):
    async def fake_current(db, owner_id):
        return _key(owner_id)

    monkeypatch.setattr(api_key_service, "current_key", fake_current)
    body = user_client.get("/api/studio/keys").json()
    assert body["prefix"] == "sk-studio_abc"
    assert "secret" not in body


def test_rotating_returns_the_secret_once(user_client, monkeypatch):
    async def fake_rotate(db, owner_id, name):
        return _key(owner_id), "sk-studio_SECRET"

    monkeypatch.setattr(api_key_service, "rotate_key", fake_rotate)
    response = user_client.post("/api/studio/keys", json={"name": "Laptop"})
    assert response.status_code == 201
    assert response.json()["secret"] == "sk-studio_SECRET"


def test_keys_need_sign_in(anon_client):
    assert anon_client.get("/api/studio/keys").status_code == 401


def test_public_route_takes_the_key_and_uses_its_owner(anon_client, monkeypatch):
    seen = {}

    async def fake_key_from_secret(db, secret):
        seen["secret"] = secret
        return _key("owner-9")

    async def fake_run(db, owner_id, operation, payload):
        seen["owner"] = owner_id
        seen["operation"] = operation
        return {"text": "1. Home"}

    monkeypatch.setattr(api_key_service, "key_from_secret", fake_key_from_secret)
    monkeypatch.setattr(whisperflow_public_routes.service, "run", fake_run)
    response = anon_client.post(
        "/api/studio/v1/whisperflow/format",
        json={"text": "point 1 home"},
        headers={"Authorization": "Bearer sk-studio_x"},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "1. Home"
    assert seen == {"secret": "sk-studio_x", "owner": "owner-9", "operation": "format"}


def test_public_route_without_a_key_is_rejected(anon_client):
    response = anon_client.post("/api/studio/v1/whisperflow/format", json={"text": "hi"})
    assert response.status_code == 404
    assert "Bearer" in response.json()["detail"]
