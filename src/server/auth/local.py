"""Single-account local login (HOST_MODE=local).

stdlib PBKDF2 + HS256 JWT. Config is read from the environment at call time
so tests can setenv without reimporting this module. Missing/invalid config
fails startup via ``require_local_auth_config`` — never silently fall back
to oss.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time
from dataclasses import dataclass

import jwt

_HASH_PREFIX = "pbkdf2"
_HASH_ALG = "sha256"
_ITERATIONS = 600_000
_DKLEN = 32
_SALT_BYTES = 16
_MIN_SECRET_LEN = 32
_DEFAULT_TTL = 7 * 24 * 3600
_ISSUER = "langalpha-local"


@dataclass(frozen=True)
class LocalAuthSettings:
    secret: str
    username: str
    password_hash: str
    user_id: str
    ttl_seconds: int


def _local_user_id() -> str:
    return os.getenv("AUTH_USER_ID", "local-dev-user")


def const_eq_str(left: str, right: str) -> bool:
    """Length-independent string compare (digest both sides, then compare)."""
    return hmac.compare_digest(
        hashlib.sha256(left.encode("utf-8")).digest(),
        hashlib.sha256(right.encode("utf-8")).digest(),
    )


def hash_password(password: str, *, iterations: int = _ITERATIONS) -> str:
    if iterations < 1:
        raise ValueError("iterations must be positive")
    salt = secrets.token_bytes(_SALT_BYTES)
    dk = hashlib.pbkdf2_hmac(
        _HASH_ALG, password.encode("utf-8"), salt, iterations, dklen=_DKLEN
    )
    return (
        f"{_HASH_PREFIX}${_HASH_ALG}${iterations}${salt.hex()}${dk.hex()}"
    )


def parse_password_hash(raw: str) -> tuple[str, int, bytes, bytes]:
    """Return (alg, iterations, salt, dk). Raises ValueError on bad format."""
    parts = raw.split("$")
    if len(parts) != 5 or parts[0] != _HASH_PREFIX:
        raise ValueError("password hash must be pbkdf2$sha256$iter$salt$dk")
    scheme, alg, iter_s, salt_hex, dk_hex = parts
    if scheme != _HASH_PREFIX or alg != _HASH_ALG:
        raise ValueError(f"unsupported password hash scheme: {scheme}${alg}")
    try:
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        dk = bytes.fromhex(dk_hex)
    except ValueError as exc:
        raise ValueError("password hash has invalid iter/salt/dk") from exc
    if iterations < 1 or not salt or not dk:
        raise ValueError("password hash fields out of range")
    return alg, iterations, salt, dk


def verify_password(password: str, encoded: str) -> bool:
    try:
        alg, iterations, salt, expected = parse_password_hash(encoded)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac(
        alg, password.encode("utf-8"), salt, iterations, dklen=len(expected)
    )
    return hmac.compare_digest(actual, expected)


def get_local_auth_settings() -> LocalAuthSettings:
    ttl_raw = os.getenv("LOCAL_AUTH_TTL_SECONDS", str(_DEFAULT_TTL)).strip()
    try:
        ttl_seconds = int(ttl_raw)
    except ValueError:
        ttl_seconds = 0
    return LocalAuthSettings(
        secret=os.getenv("LOCAL_AUTH_SECRET", "").strip(),
        username=os.getenv("LOCAL_AUTH_USERNAME", "").strip(),
        password_hash=os.getenv("LOCAL_AUTH_PASSWORD_HASH", "").strip(),
        user_id=_local_user_id(),
        ttl_seconds=ttl_seconds,
    )


def require_local_auth_config() -> LocalAuthSettings:
    settings = get_local_auth_settings()
    missing: list[str] = []
    if not settings.secret:
        missing.append("LOCAL_AUTH_SECRET")
    if not settings.username:
        missing.append("LOCAL_AUTH_USERNAME")
    if not settings.password_hash:
        missing.append("LOCAL_AUTH_PASSWORD_HASH")
    if missing:
        raise RuntimeError(
            "HOST_MODE=local requires "
            + ", ".join(missing)
            + ". Generate a hash with: "
            "uv run python scripts/utils/hash_local_password.py"
        )
    if len(settings.secret) < _MIN_SECRET_LEN:
        raise RuntimeError(
            f"LOCAL_AUTH_SECRET must be at least {_MIN_SECRET_LEN} characters "
            "(openssl rand -hex 32)"
        )
    if settings.ttl_seconds < 1:
        raise RuntimeError("LOCAL_AUTH_TTL_SECONDS must be a positive integer")
    try:
        parse_password_hash(settings.password_hash)
    except ValueError as exc:
        raise RuntimeError(
            "LOCAL_AUTH_PASSWORD_HASH is not a valid pbkdf2$sha256$… hash. "
            "Generate one with: uv run python scripts/utils/hash_local_password.py"
        ) from exc
    return settings


def issue_local_access_token(settings: LocalAuthSettings | None = None) -> str:
    cfg = settings or require_local_auth_config()
    now = int(time.time())
    payload = {
        "sub": cfg.user_id,
        "iat": now,
        "exp": now + cfg.ttl_seconds,
        "iss": _ISSUER,
    }
    return jwt.encode(payload, cfg.secret, algorithm="HS256")


def decode_local_access_token(
    token: str, settings: LocalAuthSettings | None = None
) -> str:
    """Return ``sub``. Raises ``jwt.ExpiredSignatureError`` / ``InvalidTokenError``."""
    cfg = settings or get_local_auth_settings()
    if not cfg.secret:
        raise jwt.InvalidTokenError("local auth secret is not configured")
    payload = jwt.decode(
        token,
        cfg.secret,
        algorithms=["HS256"],
        issuer=_ISSUER,
        options={"require": ["sub", "exp", "iat", "iss"]},
    )
    user_id = payload.get("sub")
    if not isinstance(user_id, str) or not user_id:
        raise jwt.InvalidTokenError("token missing sub claim")
    if user_id != cfg.user_id:
        raise jwt.InvalidTokenError("unexpected sub")
    return user_id
