"""The assistant uses Karan's PC gateway first and the LLM server as the fallback."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.services import llm_client

PC = "https://pc.example"
SERVER = "http://server2.example:8011"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    llm_client._alive.clear()
    llm_client.forget_links()

    async def links():
        return [("local", PC), ("remote", SERVER)]

    monkeypatch.setattr(llm_client, "_saved_links", links)
    yield
    llm_client._alive.clear()


def _alive(monkeypatch, pc_up: bool):
    async def is_alive(url):
        known = llm_client._alive.get(url)
        if known is not None and not known[0]:
            return False
        return pc_up if url == PC else True

    monkeypatch.setattr(llm_client, "_is_alive", is_alive)


def test_pc_first_when_it_answers(monkeypatch):
    _alive(monkeypatch, pc_up=True)
    assert asyncio.run(llm_client.gateways()) == [PC, SERVER]


def test_server_only_when_the_pc_is_down(monkeypatch):
    _alive(monkeypatch, pc_up=False)
    assert asyncio.run(llm_client.gateways()) == [SERVER]


def test_a_failed_call_skips_the_pc_for_a_while(monkeypatch):
    _alive(monkeypatch, pc_up=True)
    error = llm_client.gateway_error(httpx.ConnectError("tunnel closed"), PC)
    assert "could not answer" in error.message
    assert asyncio.run(llm_client.gateways()) == [SERVER]


def test_busy_is_not_a_failure_of_the_gateway(monkeypatch):
    _alive(monkeypatch, pc_up=True)

    class Busy(Exception):
        status_code = 429

    assert llm_client.gateway_error(Busy(), PC).code == "busy"
    assert asyncio.run(llm_client.gateways())[0] == PC


def test_rerank_falls_back_to_the_server(monkeypatch):
    _alive(monkeypatch, pc_up=True)
    calls = []

    class Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, json, headers):
            calls.append(url)
            if url.startswith(PC):
                raise httpx.ConnectError("pc offline")
            return httpx.Response(200, json={"results": [{"index": 0, "relevance_score": 0.9}]})

    monkeypatch.setattr(llm_client.httpx, "AsyncClient", Client)
    assert asyncio.run(llm_client.rerank("q", ["a"])) == [0.9]
    assert calls == [f"{PC}/rerank", f"{SERVER}/rerank"]
    assert llm_client._alive[PC][0] is False


def test_no_links_at_all_is_offline(monkeypatch):
    async def none():
        return []

    monkeypatch.setattr(llm_client, "_saved_links", none)
    with pytest.raises(Exception):
        asyncio.run(llm_client.gateways())
