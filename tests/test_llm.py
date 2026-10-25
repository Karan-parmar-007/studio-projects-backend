"""Signed-in LLM chat and text cleanup."""

from app.api.routes.llm import llm_routes


def _fake(seen, result):
    async def run(db, owner_id, text, script="roman"):
        seen["owner"] = owner_id
        seen["text"] = text
        seen["script"] = script
        return result

    return run


def test_signed_in_chat(user_client, monkeypatch):
    seen = {}
    monkeypatch.setattr(llm_routes.service, "chat", _fake(seen, {"text": "Hello"}))
    response = user_client.post("/api/studio/llm/chat", json={"text": "Hi"})
    assert response.status_code == 200
    assert response.json()["text"] == "Hello"
    assert seen["owner"] == "user-1"
    assert seen["text"] == "Hi"


def test_signed_in_format(user_client, monkeypatch):
    seen = {}
    monkeypatch.setattr(llm_routes.service, "format_text", _fake(seen, {"text": "1. Home"}))
    response = user_client.post(
        "/api/studio/llm/format",
        json={"text": "point 1 home", "script": "devanagari"},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "1. Home"
    assert seen["script"] == "devanagari"


def test_llm_needs_a_signed_in_user(anon_client):
    assert anon_client.post("/api/studio/llm/chat", json={"text": "Hi"}).status_code == 401
    assert anon_client.post("/api/studio/llm/format", json={"text": "Hi"}).status_code == 401


def test_llm_uses_only_the_oracle_link():
    from app.api.routes.llm.llm_service import oracle_only
    from app.services.routing import LinkRow

    rows = oracle_only(
        [
            LinkRow("llm", "local", "http://pc", True),
            LinkRow("speech", "local", "http://speech", True),
            LinkRow("llm", "remote", "http://oracle", True),
        ]
    )
    assert [(row.role, row.placement, row.url) for row in rows] == [("llm", "remote", "http://oracle")]


def test_format_reply_drops_script_and_tone_lines():
    from app.services.gateway import strip_control_preamble

    assert strip_control_preamble("script=roman\ntone=plain\n\nHello there") == "Hello there"


def test_closed_llm_tells_the_user_to_write(user_client, monkeypatch):
    from app.core.errors import ServiceClosedError

    async def closed(_session, _slug):
        raise ServiceClosedError(
            "This service is off right now. Contact Karan at karan.ai.engineer@gmail.com."
        )

    monkeypatch.setattr("app.api.routes.llm.llm_service.service_switches.assert_open", closed)
    response = user_client.post("/api/studio/llm/chat", json={"text": "Hi"})
    assert response.status_code == 503
    body = response.json()
    assert body["code"] == "service_off"
    assert "karan.ai.engineer@gmail.com" in body["detail"]


def test_admin_turns_a_service_off(admin_client, monkeypatch):
    seen = {}

    async def fake_set(_session, slug, enabled):
        seen["slug"] = slug
        seen["enabled"] = enabled
        return enabled

    monkeypatch.setattr("app.api.routes.services.service_routes.switches.set_enabled", fake_set)
    response = admin_client.put("/api/studio/admin/services/llm", json={"enabled": False})
    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert seen == {"slug": "llm", "enabled": False}


def test_unknown_service_switch_is_404(admin_client):
    assert admin_client.put("/api/studio/admin/services/nope", json={"enabled": False}).status_code == 404
