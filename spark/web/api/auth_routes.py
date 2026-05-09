"""Auth routes: /api/auth/login, /api/auth/logout, /api/auth/me."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from spark.web.auth import Principal, Role, get_auth, get_principal

router = APIRouter()


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class LoginResponse(BaseModel):
    role: str
    subject: str


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, response: Response) -> LoginResponse:
    auth = get_auth()
    if not auth.verify_user_password(body.username, body.password):
        raise HTTPException(status_code=401, detail="invalid credentials")
    session = auth.issue_session(body.username, Role.ADMIN)
    response.set_cookie(
        "spark_session",
        session,
        httponly=True,
        samesite="strict",
        secure=auth.cookie_secure,
        max_age=auth.cookie_max_age,
        path="/",
    )
    return LoginResponse(role=Role.ADMIN.value, subject=body.username)


@router.post("/logout")
async def logout(response: Response) -> dict[str, bool]:
    response.delete_cookie("spark_session")
    return {"ok": True}


@router.get("/me")
async def me(principal: Principal = Depends(get_principal)) -> dict[str, str]:
    return {"subject": principal.subject, "role": principal.role.value}
