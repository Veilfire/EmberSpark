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


def primary_kind(category: str) -> str:
    """The audit-log ``kind`` an operator should jump to from a category click.

    Multi-kind categories (data_class_grants) deep-link to the most
    common kind; the audit page's filter is a substring match anyway,
    so an operator can refine.
    """
    kinds = CATEGORIES.get(category, ())
    return kinds[0] if kinds else ""


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
        # Map every category to its primary audit kind so the dashboard
        # category links can deep-link to ``/audit?kind=<primary>``.
        "category_kinds": {cat: primary_kind(cat) for cat in CATEGORIES},
    }


@router.get("/offenders")
async def offenders(
    kind: str,
    hours: int = 24,
    limit: int = 5,
    _: Principal = Depends(require_viewer),
) -> dict[str, object]:
    """Top-N offenders for a given audit kind.

    Returns the most-frequent ``actor`` and ``target`` values inside
    the time window. The dashboard renders these per-category so an
    operator sees which agent / path / host is generating the noise.
    """
    since = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
    async with session_scope() as session:
        stmt = (
            select(AuditLogRow)
            .where(AuditLogRow.ts >= since)
            .where(AuditLogRow.kind == kind)
        )
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

    by_actor: dict[str, int] = {}
    by_target: dict[str, int] = {}
    for r in rows:
        if r.actor:
            by_actor[r.actor] = by_actor.get(r.actor, 0) + 1
        if r.target:
            # Strip the ``agent:plugin:`` prefix where applicable so the
            # rolled-up offenders don't all read as the same thing.
            tgt = r.target.split(":")[-1] if ":" in r.target else r.target
            by_target[tgt] = by_target.get(tgt, 0) + 1

    def top(d: dict[str, int]) -> list[dict[str, object]]:
        return [
            {"name": k, "count": v}
            for k, v in sorted(d.items(), key=lambda kv: -kv[1])[:limit]
        ]

    return {
        "kind": kind,
        "window_hours": hours,
        "total": len(rows),
        "top_actors": top(by_actor),
        "top_targets": top(by_target),
    }
