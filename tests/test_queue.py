"""A busy gateway answers 202; Studio relays it and can poll it later."""

import httpx
import pytest

from app.core.errors import NotFoundError
from app.services import gateway
from app.services.routing import LinkRow


def _rows() -> list[LinkRow]:
    return [LinkRow(role="llm", placement="local", url="http://pc", enabled=True)]


class FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, json, headers=None):
        assert url == "http://pc/format"
        return httpx.Response(
            202,
            json={"id": "job-voice", "detail": "Your request is queued.", "lane": "voice", "position": 1},
        )

    async def get(self, url, headers=None):
        assert url == "http://pc/jobs/job-voice"
        return httpx.Response(200, json={"id": "job-voice", "status": "done", "result": {"text": "1. Home"}})


@pytest.mark.asyncio
async def test_a_busy_lane_is_shown_as_queued_and_can_be_polled(monkeypatch):
    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeClient)
    gateway._JOBS.clear()
    result = await gateway.run_operation(_rows(), "llm", "/format", {"text": "hi"}, "owner", "whisperflow")
    assert result["status"] == "queued"
    assert result["poll"] == "/api/studio/v1/whisperflow/jobs/job-voice"
    body, status = await gateway.read_job("job-voice", "owner")
    assert status == 200 and body["result"]["text"] == "1. Home"


@pytest.mark.asyncio
async def test_another_user_cannot_read_my_job(monkeypatch):
    monkeypatch.setattr(gateway.httpx, "AsyncClient", FakeClient)
    gateway._JOBS.clear()
    await gateway.run_operation(_rows(), "llm", "/format", {"text": "hi"}, "owner", "whisperflow")
    with pytest.raises(NotFoundError):
        await gateway.read_job("job-voice", "someone-else")


@pytest.mark.asyncio
async def test_no_ready_link_is_a_clear_offline_error():
    rows = [LinkRow(role="llm", placement="local", url="http://pc", enabled=False)]
    with pytest.raises(gateway.ModelOfflineError) as caught:
        await gateway.run_operation(rows, "llm", "/format", {"text": "hi"}, "owner", "whisperflow")
    assert caught.value.checks[0]["state"] == "toggle_off"
