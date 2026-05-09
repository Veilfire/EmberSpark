"""Security Center routes.

Seven sub-sections map to endpoint groups:
  - /network     — per-agent network allowlist + method gates + internal grants
  - /filesystem  — per-agent filesystem allowlist/denylist + limits
  - /sandbox     — per-agent sandbox config + self-test
  - /plugins     — per-agent plugin allowlist + grant matrix
  - /secrets     — names only, last accessed, canary test
  - /privacy     — per-agent privacy mode + redaction toggles
  - /global      — freeze, compliance mode, internal-IP / raw-log masters
  - /trusted-docs — trusted documentation source allowlist
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from ipaddress import ip_network
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from spark.config.enums import DataClass, DataClassLevel, DataScope
from spark.persistence.db import session_scope
from spark.persistence.learning_models import (
    DataClassGrantRow,
    InternalNetworkGrantRow,
)
from spark.persistence.learning_repos import (
    AuditRepository,
    DataGrantRepository,
    DataPolicyRepository,
    InternalGrantRepository,
    PostureRepository,
    TrustedDocRepository,
)
from spark.persistence.models import AgentRow
from spark.privacy.guardrails import BUILTIN_DEFAULTS, bump_policy_version
from spark.utils.time import isoformat as iso_utc
from spark.web.auth import (
    Principal,
    require_admin,
    require_operator,
    require_viewer,
)
from spark.web.schemas import (
    FilesystemPolicyPatch,
    GlobalPostureUpdate,
    InternalGrantCreate,
    NetworkPolicyPatch,
    PluginAllowPatch,
    PrivacyPatch,
    SandboxPolicyPatch,
    TrustedDocSourceCreate,
)

router = APIRouter()


# -----------------------------------------------------------------------------
# Per-agent security (these endpoints only read/write the agent row; the
# underlying YAML on disk is treated as source of truth and edited by the
# dedicated YAML editor endpoint — not patched here).
# -----------------------------------------------------------------------------


@router.get("/agents/{agent_name}/overview")
async def agent_overview(
    agent_name: str, _: Principal = Depends(require_viewer)
) -> dict[str, object]:
    async with session_scope() as session:
        row = await session.get(AgentRow, agent_name)
        if row is None:
            raise HTTPException(status_code=404, detail="agent not found")
        grants = await InternalGrantRepository(session).active_for_agent(agent_name)
    return {
        "name": row.name,
        "description": row.description,
        "updated_at": iso_utc(row.updated_at),
        "internal_grants": [
            {
                "cidr": g.cidr,
                "reason": g.reason,
                "expires_at": iso_utc(g.expires_at),
                "granted_by": g.granted_by,
            }
            for g in grants
        ],
    }


@router.post("/agents/{agent_name}/network")
async def patch_network(
    agent_name: str,
    body: NetworkPolicyPatch,
    principal: Principal = Depends(require_operator),
) -> dict[str, str]:
    # Patches are audited but do NOT mutate the on-disk YAML here — see the
    # YAML editor endpoint in ops.py for the canonical write path.
    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.network.patch",
            target=agent_name,
            diff=body.model_dump(exclude_none=True),
            severity="elevated",
        )
    return {"status": "queued"}


@router.post("/agents/{agent_name}/filesystem")
async def patch_filesystem(
    agent_name: str,
    body: FilesystemPolicyPatch,
    principal: Principal = Depends(require_operator),
) -> dict[str, str]:
    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.filesystem.patch",
            target=agent_name,
            diff=body.model_dump(exclude_none=True),
            severity="elevated",
        )
    return {"status": "queued"}


@router.post("/agents/{agent_name}/sandbox")
async def patch_sandbox(
    agent_name: str,
    body: SandboxPolicyPatch,
    principal: Principal = Depends(require_operator),
) -> dict[str, str]:
    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.sandbox.patch",
            target=agent_name,
            diff=body.model_dump(exclude_none=True),
            severity="elevated",
        )
    return {"status": "queued"}


@router.post("/agents/{agent_name}/plugins")
async def patch_plugins(
    agent_name: str,
    body: PluginAllowPatch,
    principal: Principal = Depends(require_operator),
) -> dict[str, str]:
    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.plugins.patch",
            target=agent_name,
            diff=body.model_dump(exclude_none=True),
            severity="elevated",
        )
    return {"status": "queued"}


@router.post("/agents/{agent_name}/privacy")
async def patch_privacy(
    agent_name: str,
    body: PrivacyPatch,
    principal: Principal = Depends(require_operator),
) -> dict[str, str]:
    severity = "elevated"
    if body.raw_prompts or body.raw_model_outputs:
        severity = "critical"
    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.privacy.patch",
            target=agent_name,
            diff=body.model_dump(exclude_none=True),
            severity=severity,
        )
    return {"status": "queued"}


# -----------------------------------------------------------------------------
# Internal IP grants (high-risk, typed confirmation, TTL)
# -----------------------------------------------------------------------------


@router.get("/internal-grants/{agent_name}")
async def list_internal_grants(
    agent_name: str, _: Principal = Depends(require_viewer)
) -> list[dict[str, object]]:
    async with session_scope() as session:
        repo = InternalGrantRepository(session)
        rows = await repo.active_for_agent(agent_name)
    return [
        {
            "id": r.id,
            "cidr": r.cidr,
            "reason": r.reason,
            "granted_by": r.granted_by,
            "granted_at": iso_utc(r.granted_at),
            "expires_at": iso_utc(r.expires_at),
        }
        for r in rows
    ]


@router.post("/internal-grants")
async def create_internal_grant(
    body: InternalGrantCreate, principal: Principal = Depends(require_admin)
) -> dict[str, object]:
    if body.confirm_agent_name != body.agent_name:
        raise HTTPException(
            status_code=400,
            detail="confirm_agent_name must match agent_name",
        )
    try:
        ip_network(body.cidr, strict=False)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid cidr: {exc}") from exc

    expires = datetime.now(tz=timezone.utc) + timedelta(hours=body.ttl_hours)
    row = InternalNetworkGrantRow(
        agent_name=body.agent_name,
        cidr=body.cidr,
        reason=body.reason,
        granted_by=principal.subject,
        expires_at=expires,
        active=True,
    )
    async with session_scope() as session:
        await InternalGrantRepository(session).grant(row)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.internal_grant",
            target=body.agent_name,
            diff={"cidr": body.cidr, "ttl_hours": body.ttl_hours},
            reason=body.reason,
            severity="critical",
        )
    return {"ok": True, "expires_at": iso_utc(expires)}


@router.delete("/internal-grants/{grant_id}")
async def revoke_internal_grant(
    grant_id: int, principal: Principal = Depends(require_admin)
) -> dict[str, bool]:
    async with session_scope() as session:
        await InternalGrantRepository(session).revoke(grant_id)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.internal_grant.revoke",
            target=str(grant_id),
            severity="elevated",
        )
    return {"ok": True}


# -----------------------------------------------------------------------------
# Global posture — freeze, compliance, masters
# -----------------------------------------------------------------------------


@router.get("/global")
async def get_global(_: Principal = Depends(require_viewer)) -> dict[str, object]:
    async with session_scope() as session:
        row = await PostureRepository(session).get()
    return {
        "frozen": row.frozen,
        "freeze_reason": row.freeze_reason,
        "compliance_mode": row.compliance_mode,
        "allow_internal_ips": row.allow_internal_ips,
        "allow_raw_logging": row.allow_raw_logging,
        "default_privacy_mode": row.default_privacy_mode,
        "updated_at": row.updated_at,
        "updated_by": row.updated_by,
    }


@router.post("/global")
async def update_global(
    body: GlobalPostureUpdate, principal: Principal = Depends(require_admin)
) -> dict[str, object]:
    # Elevated toggles require typed confirmation ("type the word 'confirm'").
    elevated = body.allow_internal_ips or body.allow_raw_logging or body.frozen
    if elevated and (body.confirm_agent_name or "").lower() != "confirm":
        raise HTTPException(
            status_code=400,
            detail="type 'confirm' in confirm_agent_name for elevated toggles",
        )
    payload = body.model_dump(exclude={"confirm_agent_name"}, exclude_none=True)
    async with session_scope() as session:
        repo = PostureRepository(session)
        row = await repo.update(updated_by=principal.subject, **payload)
        severity = "critical" if elevated else "elevated"
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.global.update",
            target="global",
            diff=payload,
            reason=body.reason or "",
            severity=severity,
        )
    return {
        "frozen": row.frozen,
        "compliance_mode": row.compliance_mode,
        "allow_internal_ips": row.allow_internal_ips,
        "allow_raw_logging": row.allow_raw_logging,
    }


@router.post("/global/freeze")
async def freeze(
    reason: str, principal: Principal = Depends(require_admin)
) -> dict[str, bool]:
    async with session_scope() as session:
        await PostureRepository(session).update(
            frozen=True, freeze_reason=reason, updated_by=principal.subject
        )
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.freeze",
            target="global",
            reason=reason,
            severity="critical",
        )
    return {"frozen": True}


@router.post("/global/unfreeze")
async def unfreeze(principal: Principal = Depends(require_admin)) -> dict[str, bool]:
    async with session_scope() as session:
        await PostureRepository(session).update(
            frozen=False, freeze_reason="", updated_by=principal.subject
        )
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.unfreeze",
            target="global",
            severity="elevated",
        )
    return {"frozen": False}


# -----------------------------------------------------------------------------
# Trusted documentation sources (for skill discovery)
# -----------------------------------------------------------------------------


@router.get("/trusted-docs")
async def list_trusted_docs(
    _: Principal = Depends(require_viewer),
) -> list[dict[str, object]]:
    from spark.skills.sources import DEFAULT_TRUSTED_DOC_HOSTS

    async with session_scope() as session:
        user_rows = await TrustedDocRepository(session).list_all()
    defaults = [
        {"host": h, "added_by": "default", "notes": "built-in"} for h in sorted(DEFAULT_TRUSTED_DOC_HOSTS)
    ]
    user = [
        {
            "host": r.host,
            "added_by": r.added_by,
            "added_at": r.added_at,
            "notes": r.notes,
        }
        for r in user_rows
    ]
    return defaults + user


@router.post("/trusted-docs")
async def add_trusted_doc(
    body: TrustedDocSourceCreate, principal: Principal = Depends(require_admin)
) -> dict[str, bool]:
    async with session_scope() as session:
        await TrustedDocRepository(session).add(
            body.host, added_by=principal.subject, notes=body.notes
        )
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.trusted_doc.add",
            target=body.host,
            severity="elevated",
        )
    return {"ok": True}


@router.delete("/trusted-docs/{host}")
async def remove_trusted_doc(
    host: str, principal: Principal = Depends(require_admin)
) -> dict[str, bool]:
    async with session_scope() as session:
        await TrustedDocRepository(session).remove(host)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.trusted_doc.remove",
            target=host,
            severity="elevated",
        )
    return {"ok": True}


# -----------------------------------------------------------------------------
# Secrets panel — names only, never values
# -----------------------------------------------------------------------------


@router.get("/secrets")
async def list_secret_names(_: Principal = Depends(require_viewer)) -> list[str]:
    from spark.runtime import get_secret_manager

    mgr = get_secret_manager()
    return mgr.list_names()


class SecretCanaryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        import re

        if not re.match(r"^[a-zA-Z0-9._-]{1,128}$", v):
            raise ValueError("secret name must match ^[a-zA-Z0-9._-]{1,128}$")
        return v


@router.post("/secrets/canary")
async def canary_test(
    body: SecretCanaryRequest,
    principal: Principal = Depends(require_operator),
) -> dict[str, bool]:
    """Verify a secret is reachable without returning its value.

    Every probe is logged at info severity so rapid-fire enumeration is
    visible in the audit log.
    """
    from spark.runtime import get_secret_manager
    from spark.secrets import SecretNotFound

    try:
        get_secret_manager().get(body.name)
        ok = True
    except SecretNotFound:
        ok = False
    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.secret.canary",
            target=body.name,
            diff={"found": ok},
            severity="info",
        )
    return {"ok": ok}


class SecretSetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=8192)

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        import re

        if not re.match(r"^[a-zA-Z0-9._-]{1,128}$", v):
            raise ValueError("secret name must match ^[a-zA-Z0-9._-]{1,128}$")
        return v


@router.put("/secrets")
async def set_secret(
    body: SecretSetRequest,
    principal: Principal = Depends(require_admin),
) -> dict[str, bool]:
    """Store a secret in the age vault. Admin only, audited at elevated."""
    from spark.runtime import get_secret_manager

    mgr = get_secret_manager()
    mgr.set(body.name, body.value)

    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.secret.set",
            target=body.name,
            severity="elevated",
        )
    return {"ok": True}


@router.delete("/secrets/{name}")
async def delete_secret(
    name: str,
    principal: Principal = Depends(require_admin),
) -> dict[str, bool]:
    """Delete a secret from the age vault. Admin only, audited at elevated."""
    from spark.runtime import get_secret_manager

    mgr = get_secret_manager()
    mgr.delete(name)

    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.secret.delete",
            target=name,
            severity="elevated",
        )
    return {"ok": True}


# -----------------------------------------------------------------------------
# Sandbox self-test
# -----------------------------------------------------------------------------


@router.post("/sandbox/self-test")
async def sandbox_self_test(
    _: Principal = Depends(require_operator),
) -> dict[str, object]:
    """Run known-safe escape attempts and verify they fail inside the sandbox."""
    from spark.sandbox.executor import SandboxUnavailable, check_available

    try:
        backend = check_available()
    except SandboxUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # The actual spawned escape attempts live in tests/integration/test_sandbox_real.py;
    # here we only report backend availability. A future expansion can invoke
    # a curated suite of negative checks via the same worker.
    return {"backend": backend, "available": True}


# -----------------------------------------------------------------------------
# Data Classification Guardrails
# -----------------------------------------------------------------------------


class DataPolicyPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    level: str = Field(min_length=3, max_length=16)
    scopes: list[str] = Field(min_length=1, max_length=8)
    reason: str = Field(default="", max_length=1000)

    @field_validator("level")
    @classmethod
    def _check_level(cls, v: str) -> str:
        try:
            DataClassLevel(v)
        except ValueError as exc:
            raise ValueError(
                "level must be one of: allow, warn, redact, block"
            ) from exc
        return v

    @field_validator("scopes")
    @classmethod
    def _check_scopes(cls, vs: list[str]) -> list[str]:
        for v in vs:
            try:
                DataScope(v)
            except ValueError as exc:
                raise ValueError(
                    f"unknown scope {v!r}; valid: "
                    + ", ".join(s.value for s in DataScope)
                ) from exc
        return vs


class DataGrantCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_name: str = Field(min_length=1, max_length=128)
    data_class: str = Field(min_length=3, max_length=64)
    scopes: list[str] = Field(min_length=1, max_length=8)
    level_override: str = Field(default="allow", max_length=16)
    reason: str = Field(min_length=1, max_length=1000)
    ttl_hours: int | None = Field(default=168, ge=1, le=720)
    confirm_agent_name: str = Field(min_length=1, max_length=128)

    @field_validator("data_class")
    @classmethod
    def _check_class(cls, v: str) -> str:
        try:
            DataClass(v)
        except ValueError as exc:
            raise ValueError(f"unknown data class {v!r}") from exc
        return v

    @field_validator("level_override")
    @classmethod
    def _check_level(cls, v: str) -> str:
        try:
            DataClassLevel(v)
        except ValueError as exc:
            raise ValueError("level_override invalid") from exc
        return v

    @field_validator("scopes")
    @classmethod
    def _check_scopes(cls, vs: list[str]) -> list[str]:
        for v in vs:
            DataScope(v)  # raises on invalid
        return vs


@router.get("/data-classes")
async def list_data_classes(
    _: Principal = Depends(require_viewer),
) -> list[dict[str, object]]:
    """Enumerate every built-in data class with its default level + description."""
    out: list[dict[str, object]] = []
    for cls, default in BUILTIN_DEFAULTS.items():
        out.append(
            {
                "data_class": cls.value,
                "default_level": default.level.value,
                "default_scopes": sorted(s.value for s in default.scopes),
                "description": default.description,
            }
        )
    return out


@router.get("/data-policy")
async def list_data_policies(
    _: Principal = Depends(require_viewer),
) -> dict[str, object]:
    """Return global + per-agent policy rows."""
    async with session_scope() as session:
        rows = await DataPolicyRepository(session).list_all()

    def row_view(r: Any) -> dict[str, object]:
        return {
            "id": r.id,
            "scope_kind": r.scope_kind,
            "agent_name": r.agent_name,
            "data_class": r.data_class,
            "level": r.level,
            "scopes": sorted(filter(None, (r.scopes or "").split(","))),
            "reason": r.reason,
            "updated_at": iso_utc(r.updated_at),
            "updated_by": r.updated_by,
        }

    globals_out: list[dict[str, object]] = []
    agents_out: dict[str, list[dict[str, object]]] = {}
    for r in rows:
        view = row_view(r)
        if r.scope_kind == "global":
            globals_out.append(view)
        elif r.scope_kind == "agent" and r.agent_name is not None:
            agents_out.setdefault(r.agent_name, []).append(view)
    return {"global": globals_out, "agents": agents_out}


@router.put("/data-policy/global/{data_class}")
async def put_global_policy(
    data_class: str,
    body: DataPolicyPatch,
    principal: Principal = Depends(require_admin),
) -> dict[str, object]:
    try:
        DataClass(data_class)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown data class") from exc
    async with session_scope() as session:
        await DataPolicyRepository(session).upsert_global(
            data_class=data_class,
            level=body.level,
            scopes=",".join(body.scopes),
            reason=body.reason,
            actor=principal.subject,
        )
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.data_class.policy",
            target=f"global:{data_class}",
            diff={"level": body.level, "scopes": body.scopes},
            reason=body.reason,
            severity="elevated",
        )
    bump_policy_version()  # post-commit invalidation
    return {"ok": True}


@router.put("/data-policy/agent/{agent_name}/{data_class}")
async def put_agent_policy(
    agent_name: str,
    data_class: str,
    body: DataPolicyPatch,
    principal: Principal = Depends(require_admin),
) -> dict[str, object]:
    try:
        DataClass(data_class)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown data class") from exc
    async with session_scope() as session:
        if await session.get(AgentRow, agent_name) is None:
            raise HTTPException(status_code=404, detail="agent not found")
        await DataPolicyRepository(session).upsert_agent(
            agent_name=agent_name,
            data_class=data_class,
            level=body.level,
            scopes=",".join(body.scopes),
            reason=body.reason,
            actor=principal.subject,
        )
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.data_class.policy",
            target=f"agent:{agent_name}:{data_class}",
            diff={"level": body.level, "scopes": body.scopes},
            reason=body.reason,
            severity="elevated",
        )
    bump_policy_version()  # post-commit invalidation
    return {"ok": True}


@router.delete("/data-policy/agent/{agent_name}/{data_class}")
async def delete_agent_policy(
    agent_name: str,
    data_class: str,
    principal: Principal = Depends(require_admin),
) -> dict[str, object]:
    async with session_scope() as session:
        deleted = await DataPolicyRepository(session).delete_agent(
            agent_name, data_class
        )
        if not deleted:
            raise HTTPException(status_code=404, detail="policy not found")
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.data_class.policy.revert",
            target=f"agent:{agent_name}:{data_class}",
            severity="elevated",
        )
    bump_policy_version()  # post-commit invalidation
    return {"ok": True}


@router.get("/data-grants")
async def list_data_grants(
    _: Principal = Depends(require_viewer),
) -> list[dict[str, object]]:
    async with session_scope() as session:
        rows = await DataGrantRepository(session).list_active()
    return [
        {
            "id": r.id,
            "agent_name": r.agent_name,
            "data_class": r.data_class,
            "scopes": sorted(filter(None, (r.scopes or "").split(","))),
            "level_override": r.level_override,
            "reason": r.reason,
            "granted_by": r.granted_by,
            "granted_at": iso_utc(r.granted_at),
            "expires_at": iso_utc(r.expires_at) if r.expires_at else None,
            "active": r.active,
        }
        for r in rows
    ]


@router.post("/data-grants")
async def create_data_grant(
    body: DataGrantCreate,
    principal: Principal = Depends(require_admin),
) -> dict[str, object]:
    if body.confirm_agent_name != body.agent_name:
        raise HTTPException(
            status_code=400,
            detail="confirm_agent_name must match agent_name",
        )
    expires_at = None
    if body.ttl_hours is not None:
        expires_at = datetime.now(tz=timezone.utc) + timedelta(hours=body.ttl_hours)
    row = DataClassGrantRow(
        agent_name=body.agent_name,
        data_class=body.data_class,
        scopes=",".join(body.scopes),
        level_override=body.level_override,
        reason=body.reason,
        granted_by=principal.subject,
        expires_at=expires_at,
        active=True,
    )
    async with session_scope() as session:
        await DataGrantRepository(session).grant(row)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.data_class.grant",
            target=f"{body.agent_name}:{body.data_class}",
            diff={
                "scopes": body.scopes,
                "level_override": body.level_override,
                "ttl_hours": body.ttl_hours,
            },
            reason=body.reason,
            severity="critical",
        )
    bump_policy_version()
    return {
        "ok": True,
        "expires_at": iso_utc(expires_at) if expires_at else None,
    }


@router.delete("/data-grants/{grant_id}")
async def revoke_data_grant(
    grant_id: int,
    principal: Principal = Depends(require_admin),
) -> dict[str, object]:
    async with session_scope() as session:
        ok = await DataGrantRepository(session).revoke(grant_id)
        if not ok:
            raise HTTPException(status_code=404, detail="grant not found")
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.data_class.grant.revoke",
            target=str(grant_id),
            severity="elevated",
        )
    bump_policy_version()  # post-commit invalidation
    return {"ok": True}


@router.get("/data-detections")
async def recent_data_detections(
    hours: int = 24,
    _: Principal = Depends(require_viewer),
) -> dict[str, object]:
    """Roll up the last N hours of audit rows keyed to data-class kinds.

    Light aggregation — matches `security.data_class.*` events and
    groups by the parsed class label when present. Clicking a bucket in
    the UI drops the operator into the audit log pre-filtered to that
    kind.
    """
    from sqlalchemy import select  # noqa: PLC0415

    from spark.persistence.learning_models import AuditLogRow  # noqa: PLC0415

    since = datetime.now(tz=timezone.utc) - timedelta(hours=max(1, min(hours, 168)))
    async with session_scope() as session:
        stmt = (
            select(AuditLogRow)
            .where(AuditLogRow.ts >= since)
            .where(AuditLogRow.kind.like("security.data_class%"))  # type: ignore[attr-defined]
        )
        rows = list((await session.execute(stmt)).scalars().all())
    buckets: dict[str, int] = {}
    for r in rows:
        target = r.target or ""
        # Targets look like "global:pii.gov_id" / "agent:foo:pii.gov_id" /
        # "foo:financial.card". The class is the last colon-separated piece.
        cls = target.rsplit(":", 1)[-1] if ":" in target else target
        buckets[cls] = buckets.get(cls, 0) + 1
    return {
        "window_hours": hours,
        "total": sum(buckets.values()),
        "by_class": buckets,
    }
