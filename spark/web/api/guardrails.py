"""Guardrails dashboard — aggregates critical audit events for at-a-glance view."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select

from spark.persistence.db import session_scope
from spark.persistence.learning_models import AuditLogRow
from spark.web.auth import Principal, require_viewer

router = APIRouter()


# Which audit-log kinds feed into which guardrail categories.
CATEGORIES: dict[str, tuple[str, ...]] = {
    "permission_denied": ("security.permission_denied",),
    "sandbox_denied": ("sandbox.denied",),
    "budget_exceeded": ("budget.hard_stop",),
    "plugin_hash_changed": ("plugin.hash_changed",),
    "internal_grants": ("security.internal_grant",),
    "raw_logging": ("security.privacy.patch",),
    "skill_rejected": ("skill.rejected",),
    "skill_approved": ("skill.approved",),
    "data_class_policy": ("security.data_class.policy",),
    "data_class_grants": (
        "security.data_class.grant",
        "security.data_class.grant.revoke",
    ),
}


@router.get("/")
async def guardrails_window(
    hours: int = 24, _: Principal = Depends(require_viewer)
) -> dict[str, object]:
    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    async with session_scope() as session:
        stmt = select(AuditLogRow).where(AuditLogRow.ts >= since)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

    # Count by category.
    counts: dict[str, int] = {k: 0 for k in CATEGORIES}
    critical = 0
    elevated = 0
    info = 0
    for r in rows:
        for cat, kinds in CATEGORIES.items():
            if r.kind in kinds:
                counts[cat] += 1
        if r.severity == "critical":
            critical += 1
        elif r.severity == "elevated":
            elevated += 1
        else:
            info += 1

    return {
        "window_hours": hours,
        "total_events": len(rows),
        "critical": critical,
        "elevated": elevated,
        "info": info,
        "categories": counts,
    }
