import pytest
from fastapi.testclient import TestClient
from karan_sso import SsoIdentity
from karan_sso.fastapi import get_identity, require_admin

import main
from app.api import deps


async def _db():
    yield object()


@pytest.fixture
def anon_client():
    main.app.dependency_overrides[deps.get_db] = _db
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


@pytest.fixture
def user_client():
    main.app.dependency_overrides[deps.get_db] = _db
    main.app.dependency_overrides[get_identity] = lambda: SsoIdentity(user_id="user-1", email="u@example.com")
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


@pytest.fixture
def admin_client():
    main.app.dependency_overrides[deps.get_db] = _db
    main.app.dependency_overrides[get_identity] = lambda: SsoIdentity(
        user_id="admin-1", email="admin@example.com", role="owner"
    )
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()
