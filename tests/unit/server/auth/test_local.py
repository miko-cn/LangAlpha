"""HOST_MODE=local: password hash, JWT, and jwt_bearer decode branch."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi import HTTPException

from src.server.auth.local import (
    LocalAuthSettings,
    const_eq_str,
    decode_local_access_token,
    hash_password,
    issue_local_access_token,
    parse_password_hash,
    require_local_auth_config,
    verify_password,
)

_FAST = 1_000


def _hash(password: str) -> str:
    return hash_password(password, iterations=_FAST)


def _settings(**overrides) -> LocalAuthSettings:
    password = overrides.pop("password", "s3cret")
    hashed = overrides.pop("password_hash", _hash(password))
    base = {
        "secret": "a" * 32,
        "username": "admin",
        "password_hash": hashed,
        "user_id": "local-dev-user",
        "ttl_seconds": 3600,
    }
    base.update(overrides)
    return LocalAuthSettings(**base)


def test_hash_and_verify_roundtrip():
    encoded = _hash("hunter2")
    assert encoded.startswith("pbkdf2$sha256$")
    assert verify_password("hunter2", encoded)
    assert not verify_password("wrong", encoded)
    assert not verify_password("hunter2", "not-a-hash")


def test_parse_password_hash_rejects_garbage():
    with pytest.raises(ValueError):
        parse_password_hash("bcrypt$whatever")
    with pytest.raises(ValueError):
        parse_password_hash("pbkdf2$sha256$notint$aa$bb")


def test_const_eq_str_length_independent():
    assert const_eq_str("admin", "admin")
    assert not const_eq_str("admin", "ad")
    assert not const_eq_str("admin", "adminx")


def test_issue_and_decode_jwt():
    settings = _settings()
    token = issue_local_access_token(settings)
    assert decode_local_access_token(token, settings) == "local-dev-user"


def test_decode_rejects_wrong_secret_and_wrong_sub():
    settings = _settings()
    token = issue_local_access_token(settings)
    with pytest.raises(jwt.InvalidTokenError):
        decode_local_access_token(token, _settings(secret="b" * 32))

    other = _settings(user_id="someone-else")
    # Re-issue under the same secret but a different AUTH_USER_ID, then
    # decode with the original settings — sub must not be accepted.
    token_other = issue_local_access_token(other)
    with pytest.raises(jwt.InvalidTokenError):
        decode_local_access_token(token_other, settings)


def test_decode_rejects_expired_token():
    settings = _settings()
    now = datetime.now(UTC)
    token = jwt.encode(
        {
            "sub": settings.user_id,
            "iat": now - timedelta(hours=2),
            "exp": now - timedelta(hours=1),
            "iss": "langalpha-local",
        },
        settings.secret,
        algorithm="HS256",
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_local_access_token(token, settings)


def test_require_local_auth_config_fails_closed(monkeypatch):
    monkeypatch.delenv("LOCAL_AUTH_SECRET", raising=False)
    monkeypatch.delenv("LOCAL_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("LOCAL_AUTH_PASSWORD_HASH", raising=False)
    with pytest.raises(RuntimeError, match="LOCAL_AUTH_SECRET"):
        require_local_auth_config()

    monkeypatch.setenv("LOCAL_AUTH_SECRET", "short")
    monkeypatch.setenv("LOCAL_AUTH_USERNAME", "admin")
    monkeypatch.setenv("LOCAL_AUTH_PASSWORD_HASH", _hash("x"))
    with pytest.raises(RuntimeError, match="at least 32"):
        require_local_auth_config()

    monkeypatch.setenv("LOCAL_AUTH_SECRET", "a" * 32)
    monkeypatch.setenv("LOCAL_AUTH_PASSWORD_HASH", "not-valid")
    with pytest.raises(RuntimeError, match="not a valid"):
        require_local_auth_config()


def test_require_local_auth_config_ok(monkeypatch):
    monkeypatch.setenv("LOCAL_AUTH_SECRET", "a" * 32)
    monkeypatch.setenv("LOCAL_AUTH_USERNAME", "admin")
    monkeypatch.setenv("LOCAL_AUTH_PASSWORD_HASH", _hash("x"))
    monkeypatch.setenv("AUTH_USER_ID", "local-dev-user")
    cfg = require_local_auth_config()
    assert cfg.username == "admin"
    assert cfg.user_id == "local-dev-user"


@pytest.mark.asyncio
async def test_oss_verify_still_skips_token(monkeypatch):
    monkeypatch.setattr("src.server.auth.jwt_bearer.HOST_MODE", "oss")
    monkeypatch.setattr(
        "src.server.auth.jwt_bearer.LOCAL_DEV_USER_ID", "local-dev-user"
    )
    from src.server.auth.jwt_bearer import verify_jwt_token

    assert await verify_jwt_token(None) == "local-dev-user"


@pytest.mark.asyncio
async def test_local_verify_requires_and_accepts_token(monkeypatch):
    settings = _settings()
    monkeypatch.setattr("src.server.auth.jwt_bearer.HOST_MODE", "local")
    monkeypatch.setenv("LOCAL_AUTH_SECRET", settings.secret)
    monkeypatch.setenv("LOCAL_AUTH_USERNAME", settings.username)
    monkeypatch.setenv("LOCAL_AUTH_PASSWORD_HASH", settings.password_hash)
    monkeypatch.setenv("AUTH_USER_ID", settings.user_id)

    from src.server.auth.jwt_bearer import verify_jwt_token

    with pytest.raises(HTTPException) as missing:
        await verify_jwt_token(None)
    assert missing.value.status_code == 401

    token = issue_local_access_token(settings)
    creds = MagicMock()
    creds.credentials = token
    assert await verify_jwt_token(creds) == "local-dev-user"


@pytest.mark.asyncio
async def test_local_get_current_user_id_requires_token(monkeypatch):
    monkeypatch.setattr("src.server.utils.api.HOST_MODE", "local")
    from src.server.utils.api import get_current_user_id

    request = MagicMock()
    request.headers.get.return_value = None
    with pytest.raises(HTTPException) as missing:
        await get_current_user_id(request, None)
    assert missing.value.status_code == 401
