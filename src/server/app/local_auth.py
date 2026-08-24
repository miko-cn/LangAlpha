"""HOST_MODE=local login. 404 in every other mode so the route stays dark."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from src.config.settings import HOST_MODE
from src.server.auth.local import (
    const_eq_str,
    get_local_auth_settings,
    issue_local_access_token,
    verify_password,
)

router = APIRouter(prefix="/api/v1", tags=["Auth"])


class LocalLoginRequest(BaseModel):
    username: str = Field(min_length=1)
    password: str = Field(min_length=1)


class LocalLoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


@router.post("/auth/local/login", response_model=LocalLoginResponse)
async def local_login(body: LocalLoginRequest) -> LocalLoginResponse:
    if HOST_MODE != "local":
        raise HTTPException(status_code=404, detail="Not found")

    settings = get_local_auth_settings()
    # Always run both compares so a wrong username still pays the PBKDF2 cost.
    username_ok = const_eq_str(body.username, settings.username)
    password_ok = verify_password(body.password, settings.password_hash)
    if not (username_ok and password_ok):
        raise HTTPException(
            status_code=401, detail="Invalid username or password"
        )

    token = issue_local_access_token(settings)
    return LocalLoginResponse(
        access_token=token,
        expires_in=settings.ttl_seconds,
    )
