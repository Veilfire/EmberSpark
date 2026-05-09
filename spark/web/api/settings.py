"""Admin-configurable runtime settings (session timeout, etc.).

Settings persisted here override their YAML counterparts and survive
restarts. On startup ``load_dynamic_settings`` reads the row and pushes
it into the in-process singletons (``AuthState``).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from spark.persistence.db import session_scope
from spark.persistence.learning_models import SessionSettingsRow
from spark.persistence.learning_repos import AuditRepository
from spark.utils.time import utcnow
from spark.web.auth import Principal, get_auth, require_admin, require_viewer

router = APIRouter()


# 30 days — same ceiling the YAML WebConfig validates against.
MAX_TIMEOUT_SECONDS = 30 * 86_400
MIN_TIMEOUT_SECONDS = 60


class SessionSettingsView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timeout_seconds: int | None
    enabled: bool


class SessionSettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    timeout_seconds: int | None = Field(default=None)


@router.get("/session", response_model=SessionSettingsView)
async def get_session_settings(
    _: Principal = Depends(require_viewer),
) -> SessionSettingsView:
    auth = get_auth()
    ttl = auth.session_ttl_seconds
    return SessionSettingsView(timeout_seconds=ttl, enabled=ttl is not None)


@router.put("/session", response_model=SessionSettingsView)
async def update_session_settings(
    body: SessionSettingsUpdate,
    response: Response,
    principal: Principal = Depends(require_admin),
) -> SessionSettingsView:
    if body.enabled:
        if body.timeout_seconds is None:
            raise HTTPException(
                status_code=400,
                detail="timeout_seconds required when enabled=true",
            )
        if not (MIN_TIMEOUT_SECONDS <= body.timeout_seconds <= MAX_TIMEOUT_SECONDS):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"timeout_seconds must be between {MIN_TIMEOUT_SECONDS} "
                    f"and {MAX_TIMEOUT_SECONDS}"
                ),
            )
        new_ttl: int | None = body.timeout_seconds
    else:
        new_ttl = None

    async with session_scope() as session:
        row = await session.get(SessionSettingsRow, 1)
        previous: int | None
        if row is None:
            previous = None
            row = SessionSettingsRow(
                id=1,
                timeout_seconds=new_ttl,
                updated_at=utcnow(),
                updated_by=principal.subject,
            )
            session.add(row)
        else:
            previous = row.timeout_seconds
            row.timeout_seconds = new_ttl
            row.updated_at = utcnow()
            row.updated_by = principal.subject
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="settings.session_timeout",
            target="global",
            diff={"from": previous, "to": new_ttl},
            reason="session timeout updated",
            severity="elevated",
        )

    auth = get_auth()
    auth.set_session_ttl(new_ttl)

    # Re-issue the admin's session cookie so the browser picks up the new
    # Max-Age. Without this the cookie's lifetime is frozen at whatever
    # it was set to at login time — so flipping "disable timeout" on an
    # hour-old session would still have the browser evict the cookie at
    # the 1h mark, kicking the admin to /login.
    new_cookie = auth.issue_session(principal.subject, principal.role)
    response.set_cookie(
        "spark_session",
        new_cookie,
        httponly=True,
        samesite="strict",
        secure=auth.cookie_secure,
        max_age=auth.cookie_max_age,
        path="/",
    )

    return SessionSettingsView(timeout_seconds=new_ttl, enabled=new_ttl is not None)


async def load_dynamic_settings() -> None:
    """Startup hook: apply any persisted session-settings override."""
    async with session_scope() as session:
        row = await session.get(SessionSettingsRow, 1)
    if row is not None:
        get_auth().set_session_ttl(row.timeout_seconds)
