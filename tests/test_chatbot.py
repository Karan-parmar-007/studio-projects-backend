"""Chatbot + RAG routes with the real LangChain agent and a fake model (no gateway)."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.api.routes.chatbot import chatbot_routes, chatbot_service
from app.api.routes.rag import rag_routes
from app.api.routes.settings import settings_service
from app.services import llm_client
from app.services.rag import sources as source_lib


class ToolFake(GenericFakeChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def _events(response) -> list[dict]:
    return [json.loads(line) for line in response.text.splitlines() if line.strip()]


@asynccontextmanager
async def _session():
    yield object()


@pytest.fixture
def fake_stack(monkeypatch):
    replies: list[AIMessage] = []

    async def chat_model(lane, **kwargs):
        # A tool-call-only message cannot stream through the fake; real Ollama streams it fine.
        has_tools = any(getattr(m, "tool_calls", None) for m in replies)
        return ToolFake(messages=iter(replies), disable_streaming=has_tools)

    async def nothing(*args, **kwargs):
        return None

    async def no_line(*args, **kwargs):
        return 0

    settings_service.in_flight._busy.clear()
    async def chat_model_for(lane, **kwargs):
        return await chat_model(lane, **kwargs), "http://gateway.test"

    monkeypatch.setattr(llm_client, "chat_model", chat_model)
    monkeypatch.setattr(llm_client, "chat_model_for", chat_model_for)
    monkeypatch.setattr(source_lib, "active", nothing)

    async def empty_shelf(*args, **kwargs):
        return []

    monkeypatch.setattr(source_lib, "books", empty_shelf)
    monkeypatch.setattr(settings_service, "check_line", no_line)
    monkeypatch.setattr(chatbot_routes, "maker", lambda request: _session)
    monkeypatch.setattr(rag_routes, "maker", lambda request: _session)
    return replies


def test_config_for_signed_out_visitor(anon_client):
    body = anon_client.get("/api/studio/chatbot/config?surface=portfolio").json()
    assert body["enabled"] is True and body["can_use"] is True
    assert body["tools"] is False and body["signed_in"] is False
    assert body["suggestions"]


def test_signed_out_chat_streams_tokens_without_tools(anon_client, fake_stack):
    fake_stack.append(AIMessage(content="Karan is an AI engineer."))
    response = anon_client.post(
        "/api/studio/chatbot/chat", json={"message": "Who is Karan?", "surface": "portfolio"}
    )
    assert response.status_code == 200
    events = _events(response)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "status" and "sources" in kinds and kinds[-1] == "done"
    text = "".join(e["data"]["text"] for e in events if e["type"] == "token")
    assert text == "Karan is an AI engineer."
    assert "action" not in kinds


def test_signed_in_user_gets_a_maps_card_not_a_run(user_client, fake_stack, monkeypatch):
    from app.api.routes.gmaps import gmaps_service

    async def quota(session, user_id):
        return {"limit": 50, "used": 10, "remaining": 40, "day": "2026-11-10"}

    monkeypatch.setattr(gmaps_service, "quota", quota)
    fake_stack.append(
        AIMessage(
            content="",
            tool_calls=[{"name": "prepare_maps_search", "args": {"search_term": "dentists", "states": ["Texas"], "limit": 20}, "id": "c1"}],
        )
    )
    fake_stack.append(AIMessage(content="Press Run on the card to start."))
    response = user_client.post("/api/studio/chatbot/chat", json={"message": "find dentists in texas", "surface": "studio"})
    events = _events(response)
    actions = [e["data"] for e in events if e["type"] == "action"]
    assert len(actions) == 1
    assert actions[0]["kind"] == "gmaps_run"
    assert actions[0]["run"]["search_term"] == "dentists" and actions[0]["run"]["states"] == ["Texas"]
    assert "up to 20" in actions[0]["summary"]
    assert any(e["type"] == "tool" for e in events)
    assert "Press Run" in "".join(e["data"]["text"] for e in events if e["type"] == "token")


def test_signed_in_mode_refuses_visitors(anon_client, monkeypatch):
    async def load(session, project):
        return settings_service.ChatbotSettings(access="signed_in")

    monkeypatch.setattr(settings_service, "load", load)
    response = anon_client.post("/api/studio/chatbot/chat", json={"message": "hi"})
    assert response.status_code == 401
    assert response.json()["code"] == "sign_in"


def test_roles_mode(user_client, monkeypatch):
    async def load(session, project):
        return settings_service.ChatbotSettings(access="roles", roles=["studio:beta"])

    monkeypatch.setattr(settings_service, "load", load)
    response = user_client.post("/api/studio/chatbot/chat", json={"message": "hi"})
    assert response.status_code == 403


def test_surface_switch(anon_client, monkeypatch):
    async def load(session, project):
        return settings_service.ChatbotSettings(on_portfolio=False)

    monkeypatch.setattr(settings_service, "load", load)
    assert anon_client.get("/api/studio/chatbot/config?surface=portfolio").json()["enabled"] is False
    response = anon_client.post("/api/studio/chatbot/chat", json={"message": "hi", "surface": "portfolio"})
    assert response.status_code == 503


def test_busy_gateway_becomes_an_error_event(anon_client, fake_stack, monkeypatch):
    from app.core.errors import QuotaError

    async def busy(lane, **kwargs):
        raise QuotaError("The assistant is busy with other people. Try again in a minute.", code="busy")

    monkeypatch.setattr(llm_client, "chat_model", busy)
    monkeypatch.setattr(llm_client, "chat_model_for", busy)
    events = _events(anon_client.post("/api/studio/chatbot/chat", json={"message": "hi"}))
    assert events[-1]["type"] == "error" and events[-1]["data"]["code"] == "busy"


def test_role_check():
    from karan_sso import SsoIdentity

    user = SsoIdentity(user_id="u", email="u@x", role="user", roles={"studio": ["beta"]})
    assert settings_service.role_allowed(user, ["studio:beta"])
    assert settings_service.role_allowed(user, ["user"])
    assert not settings_service.role_allowed(user, ["studio:pro"])
    owner = SsoIdentity(user_id="o", email="o@x", role="owner")
    assert settings_service.role_allowed(owner, [])


def test_rag_status_and_book_missing(anon_client, fake_stack, monkeypatch):
    async def load(session, project):
        return settings_service.RagProductSettings(access="everyone")

    monkeypatch.setattr(settings_service, "load", load)
    body = anon_client.get("/api/studio/rag/status").json()
    assert body["can_use"] is True and body["book"] is None and body["books"] == [] and body["upload"] is None
    response = anon_client.post("/api/studio/rag/ask", json={"question": "What is a cell?"})
    assert response.status_code == 404


def test_rag_upload_needs_sign_in(anon_client):
    response = anon_client.post("/api/studio/rag/upload", files={"file": ("a.pdf", b"%PDF", "application/pdf")})
    assert response.status_code == 401


def test_admin_settings_round_trip_validation(admin_client, monkeypatch):
    saved = {}

    async def save(session, project, values):
        saved[project] = values
        return values

    monkeypatch.setattr(settings_service, "save", save)
    ok = admin_client.put("/api/studio/admin/services/chatbot/settings", json={"access": "roles", "roles": ["studio:beta"]})
    assert ok.status_code == 200 and ok.json()["access"] == "roles"
    bad = admin_client.put("/api/studio/admin/services/chatbot/settings", json={"access": "nobody"})
    assert bad.status_code == 422
    assert admin_client.get("/api/studio/admin/services/gmaps/settings").status_code == 404


def test_admin_book_and_upload_routes_need_admin(user_client):
    import uuid as _uuid

    some = _uuid.uuid4()
    for method, path in [
        ("get", "/api/studio/rag/admin"),
        ("delete", f"/api/studio/rag/admin/books/{some}"),
        ("delete", f"/api/studio/rag/admin/uploads/{some}"),
        ("patch", f"/api/studio/rag/admin/books/{some}"),
    ]:
        response = getattr(user_client, method)(path, **({"json": {"title": "x"}} if method == "patch" else {}))
        assert response.status_code in (401, 403), (path, response.status_code)


def test_admin_delete_removes_the_whole_source(admin_client, monkeypatch):
    import uuid as _uuid

    from app.api.routes.rag import rag_service
    from app.services.rag import sources as source_lib
    from app.services.rag.model import RagSource

    target = _uuid.uuid4()
    deleted = []

    class FakeSession:
        async def get(self, model, key):
            return RagSource(id=key, scope="upload", owner_id="u1") if key == target else None

    async def delete_source(session, source_id):
        deleted.append(source_id)

    monkeypatch.setattr(source_lib, "delete_source", delete_source)
    import asyncio

    asyncio.run(rag_service.admin_delete(FakeSession(), target, "upload"))
    assert deleted == [target]
    with pytest.raises(Exception):
        asyncio.run(rag_service.admin_delete(FakeSession(), target, "book"))  # wrong scope: refused
