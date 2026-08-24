"""POST /api/v1/auth/local/login — success, failure, dark in other modes."""

from __future__ import annotations

import jwt
import pytest
from httpx import ASGITransport, AsyncClient

from src.server.app.local_auth import router
from src.server.auth.local import hash_password
from tests.conftest import create_test_app

pytestmark = pytest.mark.asyncio

_PASSWORD = "correct-horse"
_SECRET = "z" * 32
_USERNAME = "admin"


@pytest.fixture
def local_env(monkeypatch):
    hashed = hash_password(_PASSWORD, iterations=1000)
    monkeypatch.setattr("src.server.app.local_auth.HOST_MODE", "local")
    monkeypatch.setenv("LOCAL_AUTH_SECRET", _SECRET)
    monkeypatch.setenv("LOCAL_AUTH_USERNAME", _USERNAME)
    monkeypatch.setenv("LOCAL_AUTH_PASSWORD_HASH", hashed)
    monkeypatch.setenv("AUTH_USER_ID", "local-dev-user")
    monkeypatch.setenv("LOCAL_AUTH_TTL_SECONDS", "3600")
    return hashed


async def _client():
    app = create_test_app(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def test_login_success_issues_hs256_jwt(local_env):
    async with await _client() as client:
        res = await client.post(
            "/api/v1/auth/local/login",
            json={"username": _USERNAME, "password": _PASSWORD},
        )
    assert res.status_code == 200
    body = res.json()
    assert body["token_type"] == "bearer"
    assert body["expires_in"] == 3600
    payload = jwt.decode(
        body["access_token"],
        _SECRET,
        algorithms=["HS256"],
        issuer="langalpha-local",
    )
    assert payload["sub"] == "local-dev-user"


async def test_login_wrong_password_401(local_env):
    async with await _client() as client:
        res = await client.post(
            "/api/v1/auth/local/login",
            json={"username": _USERNAME, "password": "nope"},
        )
    assert res.status_code == 401
    assert res.json()["detail"] == "Invalid username or password"


async def test_login_wrong_username_401(local_env):
    async with await _client() as client:
        res = await client.post(
            "/api/v1/auth/local/login",
            json={"username": "root", "password": _PASSWORD},
        )
    assert res.status_code == 401


async def test_login_404_when_not_local(monkeypatch):
    monkeypatch.setattr("src.server.app.local_auth.HOST_MODE", "oss")
    async with await _client() as client:
        res = await client.post(
            "/api/v1/auth/local/login",
            json={"username": _USERNAME, "password": _PASSWORD},
        )
    assert res.status_code == 404


async def test_login_404_in_platform_mode(monkeypatch):
    monkeypatch.setattr("src.server.app.local_auth.HOST_MODE", "platform")
    async with await _client() as client:
        res = await client.post(
            "/api/v1/auth/local/login",
            json={"username": _USERNAME, "password": _PASSWORD},
        )
    assert res.status_code == 404
