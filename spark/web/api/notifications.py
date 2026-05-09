"""Notification endpoints.

Drives the bell badge + drawer + toasts + preference page in the UI.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select

from spark.persistence.db import session_scope
from spark.persistence.learning_models import (
    NotificationPreferencesRow,
    NotificationRow,
)
from spark.persistence.learning_repos import AuditRepository
from spark.web.auth import Principal, require_operator, require_viewer

router = APIRouter()


# ---------------------------------------------------------------------------
# View models
# ---------------------------------------------------------------------------


class NotificationView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: int
    kind: str
    severity: str
    title: str
    body: str | None
    target_kind: str | None
    target_id: str | None
    action_url: str | None
    created_at: datetime
    read_at: datetime | None
    dismissed_at: datetime | None


class UnreadCountView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: int
    by_kind: dict[str, int]


class PreferencesView(BaseModel):
    model_config = ConfigDict(extra="forbid")
    download_ready: bool
    hitl_skill_review: bool
    hitl_approval: bool
    hitl_dlq: bool
    ip_grant_expiring: bool
    raw_logging_on: bool
    cost_soft_alert: bool
    cost_hard_stop: bool
    incident: bool
    plugin_hash_changed: bool
    memory_pruned: bool
    play_sound: bool
    toast_on_create: bool


class PreferencesUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    download_ready: bool | None = None
    hitl_skill_review: bool | None = None
    hitl_approval: bool | None = None
    hitl_dlq: bool | None = None
    ip_grant_expiring: bool | None = None
    raw_logging_on: bool | None = None
    cost_soft_alert: bool | None = None
    cost_hard_stop: bool | None = None
    incident: bool | None = None
    plugin_hash_changed: bool | None = None
    memory_pruned: bool | None = None
    play_sound: bool | None = None
    toast_on_create: bool | None = None


# ---------------------------------------------------------------------------
# List / count / mark-read / dismiss
# ---------------------------------------------------------------------------


@router.get("/")
async def list_notifications(
    unread_only: bool = Query(default=False),
    kind: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    _: Principal = Depends(require_viewer),
) -> list[NotificationView]:
    async with session_scope() as session:
        stmt = select(NotificationRow).where(NotificationRow.dismissed_at.is_(None))
        if unread_only:
            stmt = stmt.where(NotificationRow.read_at.is_(None))
        if kind is not None:
            stmt = stmt.where(NotificationRow.kind == kind)
        stmt = stmt.order_by(
            NotificationRow.read_at.is_not(None),
            NotificationRow.created_at.desc(),
        ).limit(limit)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
    return [_to_view(r) for r in rows]


@router.get("/unread-count", response_model=UnreadCountView)
async def unread_count(_: Principal = Depends(require_viewer)) -> UnreadCountView:
    async with session_scope() as session:
        total_stmt = (
            select(func.count(NotificationRow.id))
            .where(NotificationRow.read_at.is_(None))
            .where(NotificationRow.dismissed_at.is_(None))
        )
        total_result = await session.execute(total_stmt)
        total = total_result.scalar() or 0

        by_kind_stmt = (
            select(NotificationRow.kind, func.count(NotificationRow.id))
            .where(NotificationRow.read_at.is_(None))
            .where(NotificationRow.dismissed_at.is_(None))
            .group_by(NotificationRow.kind)
        )
        by_kind_result = await session.execute(by_kind_stmt)
        by_kind = {k: int(v) for k, v in by_kind_result.all()}

    return UnreadCountView(total=int(total), by_kind=by_kind)


@router.post("/{notification_id}/read")
async def mark_read(
    notification_id: int, _: Principal = Depends(require_viewer)
) -> dict[str, bool]:
    async with session_scope() as session:
        row = await session.get(NotificationRow, notification_id)
        if row is None:
            raise HTTPException(status_code=404, detail="notification not found")
        if row.read_at is None:
            row.read_at = datetime.now(tz=UTC)
    return {"ok": True}


@router.post("/{notification_id}/dismiss")
async def dismiss(
    notification_id: int, _: Principal = Depends(require_viewer)
) -> dict[str, bool]:
    async with session_scope() as session:
        row = await session.get(NotificationRow, notification_id)
        if row is None:
            raise HTTPException(status_code=404, detail="notification not found")
        now = datetime.now(tz=UTC)
        row.dismissed_at = now
        if row.read_at is None:
            row.read_at = now
    return {"ok": True}


@router.post("/read-all")
async def read_all(
    kind: str | None = Query(default=None),
    _: Principal = Depends(require_viewer),
) -> dict[str, int]:
    async with session_scope() as session:
        stmt = select(NotificationRow).where(NotificationRow.read_at.is_(None))
        if kind is not None:
            stmt = stmt.where(NotificationRow.kind == kind)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())
        now = datetime.now(tz=UTC)
        for row in rows:
            row.read_at = now
    return {"updated": len(rows)}


# ---------------------------------------------------------------------------
# Preferences
# ---------------------------------------------------------------------------


@router.get("/preferences", response_model=PreferencesView)
async def get_preferences(_: Principal = Depends(require_viewer)) -> PreferencesView:
    row = await _load_or_create_preferences()
    return _prefs_to_view(row)


@router.put("/preferences", response_model=PreferencesView)
async def update_preferences(
    body: PreferencesUpdate, principal: Principal = Depends(require_operator)
) -> PreferencesView:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        row = await _load_or_create_preferences()
        return _prefs_to_view(row)

    async with session_scope() as session:
        result = await session.execute(
            select(NotificationPreferencesRow).where(
                NotificationPreferencesRow.id == 1
            )
        )
        row = result.scalars().first()
        if row is None:
            row = NotificationPreferencesRow(id=1)
            session.add(row)
            await session.flush()
        for field_name, value in changes.items():
            setattr(row, field_name, value)
        row.updated_at = datetime.now(tz=UTC)

        await AuditRepository(session).append(
            actor=principal.subject,
            kind="notifications.preferences.update",
            target="notification_preferences",
            diff=changes,
            reason="user preference update",
            severity="info",
        )
        view = _prefs_to_view(row)
    return view


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_view(row: NotificationRow) -> NotificationView:
    return NotificationView(
        id=row.id or 0,
        kind=row.kind,
        severity=row.severity,
        title=row.title,
        body=row.body,
        target_kind=row.target_kind,
        target_id=row.target_id,
        action_url=row.action_url,
        created_at=row.created_at,
        read_at=row.read_at,
        dismissed_at=row.dismissed_at,
    )


def _prefs_to_view(row: NotificationPreferencesRow) -> PreferencesView:
    return PreferencesView(
        download_ready=row.download_ready,
        hitl_skill_review=row.hitl_skill_review,
        hitl_approval=row.hitl_approval,
        hitl_dlq=row.hitl_dlq,
        ip_grant_expiring=row.ip_grant_expiring,
        raw_logging_on=row.raw_logging_on,
        cost_soft_alert=row.cost_soft_alert,
        cost_hard_stop=row.cost_hard_stop,
        incident=row.incident,
        plugin_hash_changed=row.plugin_hash_changed,
        memory_pruned=row.memory_pruned,
        play_sound=row.play_sound,
        toast_on_create=row.toast_on_create,
    )


async def _load_or_create_preferences() -> NotificationPreferencesRow:
    async with session_scope() as session:
        result = await session.execute(
            select(NotificationPreferencesRow).where(
                NotificationPreferencesRow.id == 1
            )
        )
        row = result.scalars().first()
        if row is None:
            row = NotificationPreferencesRow(id=1)
            session.add(row)
            await session.flush()
        return row


def _json_dumps(payload: dict[str, object]) -> str:
    import json

    return json.dumps(payload, default=str, separators=(",", ":"))
