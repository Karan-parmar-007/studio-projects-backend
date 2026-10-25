"""Maps Scraper: catalog, auth, validation, streaming, quota settlement, run limits."""

from __future__ import annotations

import asyncio
import json
import threading
import uuid
from datetime import UTC, datetime

import pytest

from app.api.routes.api_key import api_key_service
from app.api.routes.api_key.model import ApiKey
from app.api.routes.gmaps import gmaps_constants, gmaps_service
from app.api.routes.gmaps.gmaps_schemas import RunIn
from app.api.routes.services import service_switches
from app.core.errors import ConflictError, QuotaError, ServiceClosedError, ValidationError


# ---------------------------------------------------------------- fakes


class Ledger:
    """Stands in for the gmaps_daily_usage table."""

    def __init__(self, used: int = 0) -> None:
        self.used = used
        self.refunds: list[int] = []
        self.refunded = asyncio.Event()

    async def reserve(self, session, user_id, day, want):
        remaining = gmaps_constants.DAILY_COMPANY_LIMIT - self.used
        if remaining <= 0:
            raise QuotaError("Daily limit reached", code="quota_exceeded")
        granted = min(want or remaining, remaining)
        self.used += granted
        return granted

    async def refund_detached(self, factory, user_id, day, amount):
        self.used -= amount
        self.refunds.append(amount)
        self.refunded.set()

    async def quota(self, session, user_id):
        limit = gmaps_constants.DAILY_COMPANY_LIMIT
        return {"limit": limit, "used": self.used, "remaining": max(0, limit - self.used), "day": "2026-10-25"}


def company(n: int) -> dict:
    return {"name": f"Company {n}", "phone": f"+1555000{n:04d}", "place_id": f"p{n}"}


def fake_runner(count: int = 3, gate: threading.Event | None = None):
    """A ScraperRunner stand-in. With a gate, it blocks until the gate opens or it is stopped."""

    class FakeRunner:
        def __init__(self, *, limit, should_stop, on_progress, **_kw):
            self.limit = limit
            self.should_stop = should_stop
            self.on_progress = on_progress

        def run(self):
            if gate is not None:
                while not gate.is_set() and not self.should_stop():
                    gate.wait(0.02)
            rows = [company(i) for i in range(count)]
            self.on_progress({"status": "running", "companies_found": len(rows), "companies_preview": rows})
            return rows

    return FakeRunner


@pytest.fixture
def ledger(monkeypatch):
    book = Ledger()
    monkeypatch.setattr(gmaps_service, "reserve", book.reserve)
    monkeypatch.setattr(gmaps_service, "refund_detached", book.refund_detached)
    monkeypatch.setattr(gmaps_service, "quota", book.quota)
    monkeypatch.setattr(gmaps_service.registry, "_runs", {})
    return book


@pytest.fixture
def open_service(monkeypatch):
    async def always_open(session, slug):
        return None

    monkeypatch.setattr(service_switches, "assert_open", always_open)


def _key(owner="owner-9") -> ApiKey:
    return ApiKey(
        id=uuid.uuid4(), owner_id=owner, name="Laptop", prefix="sk-studio_abc", key_hash="h",
        revoked=False, created_at=datetime.now(UTC), last_used_at=None,
    )


def _lines(response) -> list[dict]:
    return [json.loads(line) for line in response.iter_lines() if line]


async def _drain(stream) -> list[dict]:
    return [json.loads(chunk) async for chunk in stream]


# ---------------------------------------------------------------- catalog + auth


def test_catalog_lists_gmaps(anon_client):
    items = anon_client.get("/api/studio/projects").json()
    gmaps = next(item for item in items if item["slug"] == "gmaps")
    assert gmaps["page"] == "/products/gmaps"
    assert gmaps["playground"] is True
    paths = {endpoint["path"] for endpoint in gmaps["endpoints"]}
    assert "/api/studio/v1/gmaps/runs/stream" in paths


def test_website_routes_need_sign_in(anon_client):
    assert anon_client.get("/api/studio/gmaps/quota").status_code == 401
    assert anon_client.post("/api/studio/gmaps/runs/stream", json={"search_term": "x"}).status_code == 401


def test_public_routes_need_a_key(anon_client):
    assert anon_client.get("/api/studio/v1/gmaps/quota").status_code == 404
    response = anon_client.get(
        "/api/studio/v1/gmaps/quota", headers={"Authorization": "Bearer nope"}
    )
    assert response.status_code == 404


def test_revoked_or_unknown_key_is_refused(anon_client, monkeypatch):
    async def unknown(db, secret):
        from app.core.errors import NotFoundError

        raise NotFoundError("Unknown API key")

    monkeypatch.setattr(api_key_service, "key_from_secret", unknown)
    response = anon_client.post(
        "/api/studio/v1/gmaps/runs/stream",
        json={"search_term": "dentist"},
        headers={"Authorization": "Bearer sk-studio_revoked"},
    )
    assert response.status_code == 404


def test_quota_is_shared_by_website_and_key(user_client, monkeypatch, ledger):
    ledger.used = 20

    async def owner_is_user_1(db, secret):
        return _key("user-1")

    monkeypatch.setattr(api_key_service, "key_from_secret", owner_is_user_1)
    site = user_client.get("/api/studio/gmaps/quota").json()
    api = user_client.get("/api/studio/v1/gmaps/quota", headers={"Authorization": "Bearer sk-studio_x"}).json()
    assert site == api
    assert site["remaining"] == 30


# ---------------------------------------------------------------- locations


def test_locations_are_united_states_only(user_client):
    countries = user_client.get("/api/studio/gmaps/locations/countries").json()["items"]
    assert countries == ["United States"]
    states = user_client.get("/api/studio/gmaps/locations/states").json()["items"]
    assert "California" in states
    cities = user_client.get(
        "/api/studio/gmaps/locations/cities", params={"state": "California"}
    ).json()["items"]
    assert "Los Angeles" in cities
    assert "Houston" not in cities


# ---------------------------------------------------------------- validation


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"search_term": ""},
        {"search_term": "   "},
        {"search_term": "x" * 201},
        {"search_term": "dentist", "limit": 0},
        {"search_term": "dentist", "limit": -5},
        {"search_term": "dentist", "limit": 51},
        {"search_term": "dentist", "limit": 10**12},
        {"search_term": "dentist", "states": "California"},
        {"search_term": "dentist", "cities": [f"c{i}" for i in range(200)]},
    ],
)
def test_bad_bodies_are_rejected(user_client, open_service, ledger, body):
    response = user_client.post("/api/studio/gmaps/runs/stream", json=body)
    assert response.status_code == 422
    assert ledger.used == 0


def test_malformed_json_is_rejected(user_client, open_service, ledger):
    response = user_client.post(
        "/api/studio/gmaps/runs/stream",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 422


@pytest.mark.parametrize(
    "body, unknown",
    [
        ({"search_term": "dentist", "countries": ["Narnia"]}, "Narnia"),
        ({"search_term": "dentist", "states": ["Atlantis"]}, "Atlantis"),
        ({"search_term": "dentist", "cities": ["Gotham"]}, "Gotham"),
        ({"search_term": "dentist", "states": ["Texas"], "cities": ["Los Angeles"]}, "Los Angeles"),
    ],
)
def test_unknown_places_are_named(user_client, open_service, ledger, body, unknown):
    response = user_client.post("/api/studio/gmaps/runs/stream", json=body)
    assert response.status_code == 422
    assert unknown in response.json()["detail"]
    assert ledger.used == 0


def test_state_codes_and_case_are_accepted():
    run = gmaps_service.check_run(RunIn(search_term=" dentist ", states=["ca"], cities=["los angeles"]))
    assert run.search_term == "dentist"


# ---------------------------------------------------------------- streaming (HTTP)


def test_website_run_streams_ndjson(user_client, open_service, ledger, monkeypatch):
    monkeypatch.setattr(gmaps_service, "ScraperRunner", fake_runner(3))
    response = user_client.post(
        "/api/studio/gmaps/runs/stream", json={"search_term": "dentist", "states": ["California"]}
    )
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert response.headers["x-accel-buffering"] == "no"
    events = _lines(response)
    kinds = [event["type"] for event in events]
    assert kinds[0] == "started"
    assert kinds.count("company") == 3
    assert kinds[-1] == "completed"


def test_public_run_uses_key_owner(anon_client, open_service, ledger, monkeypatch):
    seen = {}

    async def fake_key(db, secret):
        seen["secret"] = secret
        return _key("owner-9")

    monkeypatch.setattr(api_key_service, "key_from_secret", fake_key)
    monkeypatch.setattr(gmaps_service, "ScraperRunner", fake_runner(2))
    original = gmaps_service.registry.claim

    def spy(user_id):
        seen["owner"] = user_id
        return original(user_id)

    monkeypatch.setattr(gmaps_service.registry, "claim", spy)
    response = anon_client.post(
        "/api/studio/v1/gmaps/runs/stream",
        json={"search_term": "plumber", "limit": 2},
        headers={"Authorization": "Bearer sk-studio_SECRET"},
    )
    assert response.status_code == 200
    assert seen == {"secret": "sk-studio_SECRET", "owner": "owner-9"}
    assert [e["type"] for e in _lines(response)].count("company") == 2


def test_service_off_returns_503(user_client, ledger, monkeypatch):
    async def closed(session, slug):
        raise ServiceClosedError("off")

    monkeypatch.setattr(service_switches, "assert_open", closed)
    response = user_client.post("/api/studio/gmaps/runs/stream", json={"search_term": "dentist"})
    assert response.status_code == 503
    assert ledger.used == 0


def test_quota_exhausted_returns_429_and_frees_the_slot(user_client, open_service, ledger):
    ledger.used = gmaps_constants.DAILY_COMPANY_LIMIT
    response = user_client.post("/api/studio/gmaps/runs/stream", json={"search_term": "dentist"})
    assert response.status_code == 429
    assert response.json()["code"] == "quota_exceeded"
    assert gmaps_service.registry.active() == 0


# ---------------------------------------------------------------- service-level runs


async def _start(user="u1", limit=None, **extra):
    body = RunIn(search_term="dentist", limit=limit, **extra)
    return await gmaps_service.start_run(object(), None, user, body)


async def _settled(ledger, timeout=5):
    await asyncio.wait_for(ledger.refunded.wait(), timeout)


async def test_limit_reserves_only_what_was_asked(ledger, open_service, monkeypatch):
    monkeypatch.setattr(gmaps_service, "ScraperRunner", fake_runner(10))
    events = await _drain(await _start(limit=4))
    assert [e["type"] for e in events].count("company") == 4
    assert events[0]["data"]["limit"] == 4
    await _wait_released()
    assert ledger.used == 4


async def test_unused_quota_is_refunded(ledger, open_service, monkeypatch):
    monkeypatch.setattr(gmaps_service, "ScraperRunner", fake_runner(3))
    events = await _drain(await _start())
    assert [e["type"] for e in events].count("company") == 3
    await _settled(ledger)
    assert ledger.refunds == [gmaps_constants.DAILY_COMPANY_LIMIT - 3]
    assert ledger.used == 3
    await _wait_released()


async def test_limit_larger_than_remaining_is_capped(ledger, open_service, monkeypatch):
    ledger.used = 45
    monkeypatch.setattr(gmaps_service, "ScraperRunner", fake_runner(20))
    events = await _drain(await _start(limit=50))
    assert events[0]["data"]["limit"] == 5
    assert [e["type"] for e in events].count("company") == 5
    await _wait_released()
    assert ledger.used == 50


async def test_second_run_for_same_user_is_409(ledger, open_service, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(gmaps_service, "ScraperRunner", fake_runner(1, gate))
    first = await _start("same")
    with pytest.raises(ConflictError):
        await _start("same")
    gate.set()
    await _drain(first)
    await _wait_released()


async def test_racing_starts_for_same_user_only_one_wins(ledger, open_service, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(gmaps_service, "ScraperRunner", fake_runner(1, gate))
    results = await asyncio.gather(_start("racer"), _start("racer"), return_exceptions=True)
    streams = [r for r in results if not isinstance(r, Exception)]
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(streams) == 1 and isinstance(errors[0], ConflictError)
    gate.set()
    await _drain(streams[0])
    await _wait_released()


async def test_global_cap_returns_busy(ledger, open_service, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(gmaps_service, "ScraperRunner", fake_runner(1, gate))
    streams = [await _start(f"user-{i}", limit=1) for i in range(gmaps_constants.MAX_ACTIVE_RUNS)]
    with pytest.raises(QuotaError) as caught:
        await _start("one-too-many", limit=1)
    assert caught.value.code == "busy"
    gate.set()
    for stream in streams:
        await _drain(stream)
    await _wait_released()


async def test_switching_off_stops_running_scrapes(ledger, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(gmaps_service, "ScraperRunner", fake_runner(5, gate))

    async def always_open(session, slug):
        return None

    monkeypatch.setattr(service_switches, "assert_open", always_open)
    stream = await _start("u-off")
    first = json.loads(await anext(stream))
    assert first["type"] == "started"

    class FakeSession:
        async def get(self, model, key):
            return None

        def add(self, row):
            pass

        async def commit(self):
            pass

    await service_switches.set_enabled(FakeSession(), "gmaps", False)
    rest = [json.loads(chunk) async for chunk in stream]
    assert any(e["type"] == "error" and "off" in e["data"]["message"].lower() for e in rest)
    await _settled(ledger)
    await _wait_released()
    assert ledger.used <= 5


async def test_client_disconnect_stops_and_refunds(ledger, open_service, monkeypatch):
    gate = threading.Event()
    monkeypatch.setattr(gmaps_service, "ScraperRunner", fake_runner(5, gate))
    stream = await _start("leaver")
    await anext(stream)
    await stream.aclose()
    await _settled(ledger)
    await _wait_released()
    assert ledger.used == 0


async def test_stream_that_is_never_read_is_stopped(ledger, open_service, monkeypatch):
    monkeypatch.setattr(gmaps_constants, "ATTACH_TIMEOUT_SECONDS", 0.1)
    gate = threading.Event()
    monkeypatch.setattr(gmaps_service, "ScraperRunner", fake_runner(5, gate))
    stream = await _start("ghost")
    await _settled(ledger)
    await _wait_released()
    assert ledger.used == 0
    await stream.aclose()


async def test_engine_crash_reports_error_and_refunds(ledger, open_service, monkeypatch):
    class Boom:
        def __init__(self, **_kw):
            pass

        def run(self):
            raise RuntimeError("blocked by Google")

    monkeypatch.setattr(gmaps_service, "ScraperRunner", Boom)
    events = await _drain(await _start("crash"))
    assert events[-1]["type"] == "error"
    await _settled(ledger)
    await _wait_released()
    assert ledger.used == 0


async def _wait_released(timeout=5):
    async def gone():
        while gmaps_service.registry.active():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(gone(), timeout)


def test_check_run_rejects_empty_location_match():
    with pytest.raises(ValidationError):
        gmaps_service.check_run(RunIn(search_term="dentist", countries=["Narnia"]))
