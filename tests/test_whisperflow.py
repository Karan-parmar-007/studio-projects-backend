"""Signed-in WhisperFlow playground: SSO cookie instead of an API key."""

from app.api.routes.whisperflow import whisperflow_routes


def _fake_run(seen, result):
    async def run(db, owner_id, operation, payload):
        seen["owner"] = owner_id
        seen["operation"] = operation
        seen["payload"] = payload
        return result

    return run


def test_signed_in_user_formats_without_a_key(user_client, monkeypatch):
    seen = {}
    monkeypatch.setattr(whisperflow_routes.service, "run", _fake_run(seen, {"text": "1. Home"}))
    response = user_client.post("/api/studio/whisperflow/format", json={"text": "point 1 home"})
    assert response.status_code == 200
    assert response.json()["text"] == "1. Home"
    assert seen["owner"] == "user-1" and seen["operation"] == "format"


def test_queued_answer_is_a_202(user_client, monkeypatch):
    seen = {}
    queued = {"status": "queued", "job_id": "j1", "poll": "/api/studio/v1/whisperflow/jobs/j1"}
    monkeypatch.setattr(whisperflow_routes.service, "run", _fake_run(seen, queued))
    response = user_client.post("/api/studio/whisperflow/transcribe", json={"audio_base64": "UklGRg=="})
    assert response.status_code == 202
    assert response.json()["job_id"] == "j1"
    assert seen["operation"] == "transcribe"


def test_empty_audio_is_rejected(user_client):
    response = user_client.post("/api/studio/whisperflow/transcribe", json={"audio_base64": "  "})
    assert response.status_code == 422


def test_playground_needs_a_signed_in_user(anon_client):
    assert anon_client.post("/api/studio/whisperflow/format", json={"text": "hi"}).status_code == 401


def test_links_requires_login(anon_client):
    assert anon_client.get("/api/studio/whisperflow/links").status_code == 401


def test_links_requires_admin_role(user_client):
    assert user_client.get("/api/studio/whisperflow/links").status_code == 403


def test_links_allowed_for_admin(admin_client, monkeypatch):
    async def fake_links(db, user_id):
        return []

    monkeypatch.setattr(whisperflow_routes.service, "links", fake_links)
    res = admin_client.get("/api/studio/whisperflow/links")
    assert res.status_code == 200
