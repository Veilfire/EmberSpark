"""Repositories for learning, skills, cost, audit, and global posture."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from spark.persistence.learning_models import (
    AnnotationRow,
    AuditLogRow,
    BudgetRow,
    CostEventRow,
    DataClassGrantRow,
    DataClassPolicyRow,
    GlobalPostureRow,
    IncidentAckRow,
    InternalNetworkGrantRow,
    ModelCallEventRow,
    PersonaRow,
    PlaybookRow,
    PlaybookRunRow,
    PluginConfigRow,
    RunSpanRow,
    SkillReviewRow,
    SkillRow,
    TriggerRow,
    TrustedDocSourceRow,
)


def _now() -> datetime:
    return datetime.now(tz=UTC)


# Sentinel used by upsert kwargs that distinguish "leave the column
# alone" from "set the column to NULL". ``None`` is a valid stored
# value (e.g. clear a mask_style override), so we need a separate
# marker for "argument not provided".
_UNSET: Any = object()


class PlaybookRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, row: PlaybookRow) -> None:
        existing = await self.session.get(PlaybookRow, row.playbook_id)
        if existing is None:
            self.session.add(row)
            return
        for field in (
            "agent_name",
            "name",
            "description",
            "fingerprint",
            "applicability_summary",
            "tool_sequence",
            "alpha",
            "beta",
            "uses",
            "last_success_at",
            "last_used_at",
            "avg_duration_seconds",
            "avg_tool_calls",
            "avg_model_calls",
        ):
            setattr(existing, field, getattr(row, field))
        existing.updated_at = _now()

    async def get(self, playbook_id: str) -> PlaybookRow | None:
        return await self.session.get(PlaybookRow, playbook_id)

    async def find_by_fingerprint(
        self, agent_name: str, fingerprint: str
    ) -> PlaybookRow | None:
        stmt = select(PlaybookRow).where(
            (PlaybookRow.agent_name == agent_name)
            & (PlaybookRow.fingerprint == fingerprint)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_for_agent(self, agent_name: str) -> list[PlaybookRow]:
        stmt = select(PlaybookRow).where(PlaybookRow.agent_name == agent_name)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def record_run(self, row: PlaybookRunRow) -> None:
        self.session.add(row)


class SkillRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, row: SkillRow) -> None:
        existing = await self.session.get(SkillRow, row.skill_id)
        if existing is None:
            self.session.add(row)
            return
        for field in (
            "name",
            "description",
            "service_name",
            "base_url",
            "auth_method",
            "required_secrets",
            "required_hosts",
            "confidence",
            "status",
        ):
            setattr(existing, field, getattr(row, field))
        existing.updated_at = _now()

    async def get(self, skill_id: str) -> SkillRow | None:
        return await self.session.get(SkillRow, skill_id)

    async def list_for_agent(
        self, agent_name: str, *, include_disabled: bool = False
    ) -> list[SkillRow]:
        stmt = select(SkillRow).where(SkillRow.agent_name == agent_name)
        if not include_disabled:
            stmt = stmt.where(SkillRow.status == "approved")
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_namespace(self, namespace: str) -> list[SkillRow]:
        stmt = select(SkillRow).where(SkillRow.namespace == namespace)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def mark_used(self, skill_id: str) -> None:
        row = await self.session.get(SkillRow, skill_id)
        if row is None:
            return
        row.uses += 1
        row.last_used_at = _now()

    async def disable(self, skill_id: str) -> None:
        row = await self.session.get(SkillRow, skill_id)
        if row is None:
            return
        row.status = "disabled"
        row.updated_at = _now()


class SkillReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(self, row: SkillReviewRow) -> None:
        self.session.add(row)

    async def get(self, review_id: str) -> SkillReviewRow | None:
        return await self.session.get(SkillReviewRow, review_id)

    async def list_pending(self, agent_name: str | None = None) -> list[SkillReviewRow]:
        stmt = select(SkillReviewRow).where(SkillReviewRow.state == "pending")
        if agent_name is not None:
            stmt = stmt.where(SkillReviewRow.agent_name == agent_name)
        stmt = stmt.order_by(SkillReviewRow.discovered_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_all(self, limit: int = 200) -> list[SkillReviewRow]:
        stmt = (
            select(SkillReviewRow)
            .order_by(SkillReviewRow.discovered_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def resolve(
        self,
        review_id: str,
        *,
        state: str,
        reviewer: str,
        notes: str | None = None,
    ) -> SkillReviewRow | None:
        row = await self.session.get(SkillReviewRow, review_id)
        if row is None:
            return None
        row.state = state
        row.reviewer = reviewer
        row.review_notes = notes
        row.reviewed_at = _now()
        return row


class CostRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(self, row: CostEventRow) -> None:
        self.session.add(row)

    async def total_usd(
        self,
        *,
        since: datetime | None = None,
        scope: str = "*",
        scope_key: str | None = None,
    ) -> float:
        stmt = select(CostEventRow)
        if since is not None:
            stmt = stmt.where(CostEventRow.recorded_at >= since)
        if scope == "agent" and scope_key is not None:
            stmt = stmt.where(CostEventRow.agent_name == scope_key)
        elif scope == "provider" and scope_key is not None:
            stmt = stmt.where(CostEventRow.provider == scope_key)
        result = await self.session.execute(stmt)
        return sum(float(r.total_cost_usd) for r in result.scalars().all())


class ModelCallEventRepository:
    """Per-model-call telemetry rows — one per planner iteration."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def record(self, row: ModelCallEventRow) -> ModelCallEventRow:
        self.session.add(row)
        await self.session.flush()  # populate row.id for caller
        return row

    async def list_for_run(self, run_id: str) -> list[ModelCallEventRow]:
        stmt = (
            select(ModelCallEventRow)
            .where(ModelCallEventRow.run_id == run_id)
            .order_by(ModelCallEventRow.sequence.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def update_from_enrichment(
        self,
        *,
        row_id: int,
        cost_usd: float | None,
        raw_metadata_merge: dict[str, Any],
    ) -> None:
        """Apply OpenRouter post-hoc enrichment to an existing row.

        Sets ``cost_source='reported'`` and merges the enrichment payload
        into ``raw_metadata_json`` under a top-level ``"openrouter_enriched"``
        key so the original capture stays inspectable.
        """
        row = await self.session.get(ModelCallEventRow, row_id)
        if row is None:
            return
        if cost_usd is not None:
            row.cost_usd = cost_usd
            row.cost_source = "reported"
        existing: dict[str, Any] = {}
        if row.raw_metadata_json:
            try:
                existing = json.loads(row.raw_metadata_json) or {}
            except json.JSONDecodeError:
                existing = {}
        existing["openrouter_enriched"] = raw_metadata_merge
        row.raw_metadata_json = json.dumps(existing, default=str)


class BudgetRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def upsert(self, row: BudgetRow) -> None:
        existing = await self.session.get(BudgetRow, row.budget_id)
        if existing is None:
            self.session.add(row)
            return
        for field in ("limit_usd", "soft_alert_usd", "hard_stop", "enabled"):
            setattr(existing, field, getattr(row, field))
        existing.updated_at = _now()

    async def list_all(self) -> list[BudgetRow]:
        stmt = select(BudgetRow)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


class AuditRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def append(
        self,
        *,
        actor: str,
        kind: str,
        target: str,
        diff: Any = None,
        reason: str = "",
        severity: str = "info",
        correlation_id: str | None = None,
    ) -> None:
        payload = json.dumps(diff, default=str) if diff is not None else ""
        self.session.add(
            AuditLogRow(
                actor=actor,
                kind=kind,
                target=target,
                diff=payload,
                reason=reason,
                severity=severity,
                correlation_id=correlation_id,
            )
        )
        # Surface critical entries as an INCIDENT notification so the bell
        # + drawer + toast fire in the web UI. Non-fatal on failure.
        if severity == "critical":
            try:
                from spark.notifications import (
                    NotificationKind,
                    get_notification_service,
                )

                await get_notification_service().notify(
                    NotificationKind.INCIDENT,
                    title=f"Incident: {kind}",
                    body=reason or f"Critical audit entry on {target}",
                    severity="critical",
                    target_kind="audit",
                    target_id=target,
                    action_url=f"/audit?kind={kind}",
                )
            except Exception:  # pragma: no cover — audit path is hot
                pass

    async def list_recent(
        self,
        *,
        limit: int = 200,
        kind: str | None = None,
        min_severity: str | None = None,
    ) -> list[AuditLogRow]:
        stmt = select(AuditLogRow).order_by(AuditLogRow.ts.desc()).limit(limit)
        if kind is not None:
            stmt = stmt.where(AuditLogRow.kind == kind)
        result = await self.session.execute(stmt)
        rows = list(result.scalars().all())
        if min_severity is not None:
            order = {"info": 0, "elevated": 1, "critical": 2}
            threshold = order.get(min_severity, 0)
            rows = [r for r in rows if order.get(r.severity, 0) >= threshold]
        return rows


class PostureRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self) -> GlobalPostureRow:
        row = await self.session.get(GlobalPostureRow, 1)
        if row is None:
            row = GlobalPostureRow(id=1)
            self.session.add(row)
            await self.session.flush()
        return row

    async def update(self, **fields: Any) -> GlobalPostureRow:
        row = await self.get()
        for k, v in fields.items():
            setattr(row, k, v)
        row.updated_at = _now()
        return row


class TrustedDocRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[TrustedDocSourceRow]:
        result = await self.session.execute(select(TrustedDocSourceRow))
        return list(result.scalars().all())

    async def add(self, host: str, added_by: str, notes: str = "") -> None:
        existing = await self.session.get(TrustedDocSourceRow, host)
        if existing is not None:
            return
        self.session.add(
            TrustedDocSourceRow(host=host.lower().strip(), added_by=added_by, notes=notes)
        )

    async def remove(self, host: str) -> None:
        row = await self.session.get(TrustedDocSourceRow, host.lower().strip())
        if row is not None:
            await self.session.delete(row)


class InternalGrantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def grant(self, row: InternalNetworkGrantRow) -> None:
        self.session.add(row)

    async def active_for_agent(self, agent_name: str) -> list[InternalNetworkGrantRow]:
        now = _now()
        stmt = select(InternalNetworkGrantRow).where(
            (InternalNetworkGrantRow.agent_name == agent_name)
            & (InternalNetworkGrantRow.active)
            & (InternalNetworkGrantRow.expires_at > now)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def revoke(self, grant_id: int) -> None:
        row = await self.session.get(InternalNetworkGrantRow, grant_id)
        if row is not None:
            row.active = False


# ---------------------------------------------------------------------------
# Data Classification Guardrails
# ---------------------------------------------------------------------------


class DataPolicyRepository:
    """CRUD over global + per-agent data-class policy rows."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_global(self, data_class: str) -> DataClassPolicyRow | None:
        stmt = select(DataClassPolicyRow).where(
            (DataClassPolicyRow.scope_kind == "global")
            & (DataClassPolicyRow.data_class == data_class)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def get_agent(
        self, agent_name: str, data_class: str
    ) -> DataClassPolicyRow | None:
        stmt = select(DataClassPolicyRow).where(
            (DataClassPolicyRow.scope_kind == "agent")
            & (DataClassPolicyRow.agent_name == agent_name)
            & (DataClassPolicyRow.data_class == data_class)
        )
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def list_all(self) -> list[DataClassPolicyRow]:
        stmt = select(DataClassPolicyRow).order_by(
            DataClassPolicyRow.scope_kind, DataClassPolicyRow.data_class
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_for_agent(self, agent_name: str) -> list[DataClassPolicyRow]:
        stmt = select(DataClassPolicyRow).where(
            (DataClassPolicyRow.scope_kind == "agent")
            & (DataClassPolicyRow.agent_name == agent_name)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def upsert_global(
        self,
        *,
        data_class: str,
        level: str,
        scopes: str,
        reason: str,
        actor: str,
        mask_style: str | None = _UNSET,  # type: ignore[assignment]
        min_confidence: float | None = _UNSET,  # type: ignore[assignment]
        require_consensus: bool | None = _UNSET,  # type: ignore[assignment]
        detector_overrides_json: str | None = _UNSET,  # type: ignore[assignment]
    ) -> DataClassPolicyRow:
        row = await self.get_global(data_class)
        if row is None:
            row = DataClassPolicyRow(
                scope_kind="global",
                agent_name=None,
                data_class=data_class,
                level=level,
                scopes=scopes,
                reason=reason,
                mask_style=None if mask_style is _UNSET else mask_style,
                min_confidence=None if min_confidence is _UNSET else min_confidence,
                require_consensus=None if require_consensus is _UNSET else require_consensus,
                detector_overrides_json=(
                    None if detector_overrides_json is _UNSET else detector_overrides_json
                ),
                updated_at=datetime.now(tz=UTC),
                updated_by=actor,
            )
            self.session.add(row)
        else:
            row.level = level
            row.scopes = scopes
            row.reason = reason
            if mask_style is not _UNSET:
                row.mask_style = mask_style
            if min_confidence is not _UNSET:
                row.min_confidence = min_confidence
            if require_consensus is not _UNSET:
                row.require_consensus = require_consensus
            if detector_overrides_json is not _UNSET:
                row.detector_overrides_json = detector_overrides_json
            row.updated_at = datetime.now(tz=UTC)
            row.updated_by = actor
        return row

    async def upsert_agent(
        self,
        *,
        agent_name: str,
        data_class: str,
        level: str,
        scopes: str,
        reason: str,
        actor: str,
        mask_style: str | None = _UNSET,  # type: ignore[assignment]
        min_confidence: float | None = _UNSET,  # type: ignore[assignment]
        require_consensus: bool | None = _UNSET,  # type: ignore[assignment]
        detector_overrides_json: str | None = _UNSET,  # type: ignore[assignment]
    ) -> DataClassPolicyRow:
        row = await self.get_agent(agent_name, data_class)
        if row is None:
            row = DataClassPolicyRow(
                scope_kind="agent",
                agent_name=agent_name,
                data_class=data_class,
                level=level,
                scopes=scopes,
                reason=reason,
                mask_style=None if mask_style is _UNSET else mask_style,
                min_confidence=None if min_confidence is _UNSET else min_confidence,
                require_consensus=None if require_consensus is _UNSET else require_consensus,
                detector_overrides_json=(
                    None if detector_overrides_json is _UNSET else detector_overrides_json
                ),
                updated_at=datetime.now(tz=UTC),
                updated_by=actor,
            )
            self.session.add(row)
        else:
            row.level = level
            row.scopes = scopes
            row.reason = reason
            if mask_style is not _UNSET:
                row.mask_style = mask_style
            if min_confidence is not _UNSET:
                row.min_confidence = min_confidence
            if require_consensus is not _UNSET:
                row.require_consensus = require_consensus
            if detector_overrides_json is not _UNSET:
                row.detector_overrides_json = detector_overrides_json
            row.updated_at = datetime.now(tz=UTC)
            row.updated_by = actor
        return row

    async def delete_agent(self, agent_name: str, data_class: str) -> bool:
        row = await self.get_agent(agent_name, data_class)
        if row is None:
            return False
        await self.session.delete(row)
        return True


class DataGrantRepository:
    """CRUD over per-agent (or global) unlimited data-class grants."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def grant(self, row: DataClassGrantRow) -> None:
        self.session.add(row)

    async def list_active(self) -> list[DataClassGrantRow]:
        now = datetime.now(tz=UTC)
        stmt = select(DataClassGrantRow).where(
            DataClassGrantRow.active
            & (
                (DataClassGrantRow.expires_at.is_(None))  # type: ignore[attr-defined]
                | (DataClassGrantRow.expires_at > now)
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def active_for_agent(
        self, agent_name: str
    ) -> list[DataClassGrantRow]:
        now = datetime.now(tz=UTC)
        stmt = select(DataClassGrantRow).where(
            DataClassGrantRow.active
            & (
                (DataClassGrantRow.agent_name == agent_name)
                | (DataClassGrantRow.agent_name == "__all__")
            )
            & (
                (DataClassGrantRow.expires_at.is_(None))  # type: ignore[attr-defined]
                | (DataClassGrantRow.expires_at > now)
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def revoke(self, grant_id: int) -> bool:
        row = await self.session.get(DataClassGrantRow, grant_id)
        if row is None:
            return False
        row.active = False
        return True


# ---------------------------------------------------------------------------
# F1 — Plugin config
# ---------------------------------------------------------------------------


class PluginConfigRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, plugin_name: str) -> PluginConfigRow | None:
        return await self.session.get(PluginConfigRow, plugin_name)

    async def list_all(self) -> list[PluginConfigRow]:
        result = await self.session.execute(select(PluginConfigRow))
        return list(result.scalars().all())

    async def upsert(
        self,
        *,
        plugin_name: str,
        config_json: str,
        schema_hash: str,
        updated_by: str | None,
    ) -> PluginConfigRow:
        existing = await self.session.get(PluginConfigRow, plugin_name)
        if existing is None:
            row = PluginConfigRow(
                plugin_name=plugin_name,
                config_json=config_json,
                schema_hash=schema_hash,
                updated_by=updated_by,
            )
            self.session.add(row)
            return row
        existing.config_json = config_json
        existing.schema_hash = schema_hash
        existing.updated_by = updated_by
        existing.updated_at = _now()
        return existing

    async def delete(self, plugin_name: str) -> bool:
        row = await self.session.get(PluginConfigRow, plugin_name)
        if row is None:
            return False
        await self.session.delete(row)
        return True


# ---------------------------------------------------------------------------
# F2 — Personas
# ---------------------------------------------------------------------------


class PersonaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, persona_id: str) -> PersonaRow | None:
        return await self.session.get(PersonaRow, persona_id)

    async def list_all(self) -> list[PersonaRow]:
        result = await self.session.execute(
            select(PersonaRow).order_by(PersonaRow.updated_at.desc())
        )
        return list(result.scalars().all())

    async def get_active(self) -> PersonaRow | None:
        stmt = select(PersonaRow).where(PersonaRow.is_active)
        result = await self.session.execute(stmt)
        return result.scalars().first()

    async def upsert(self, row: PersonaRow) -> PersonaRow:
        existing = await self.session.get(PersonaRow, row.persona_id)
        if existing is None:
            self.session.add(row)
            return row
        for field in ("name", "description", "system_prompt", "tone", "tags"):
            setattr(existing, field, getattr(row, field))
        existing.updated_at = _now()
        return existing

    async def activate(self, persona_id: str) -> PersonaRow | None:
        """Flip exactly one row to active; de-activate all others atomically."""
        target = await self.session.get(PersonaRow, persona_id)
        if target is None:
            return None
        # De-activate every other row first.
        result = await self.session.execute(
            select(PersonaRow).where(PersonaRow.is_active)
        )
        for row in result.scalars().all():
            if row.persona_id != persona_id:
                row.is_active = False
                row.updated_at = _now()
        target.is_active = True
        target.updated_at = _now()
        return target

    async def delete(self, persona_id: str) -> bool:
        row = await self.session.get(PersonaRow, persona_id)
        if row is None:
            return False
        if row.is_active:
            # Never orphan the runtime — refuse to delete the active persona.
            raise ValueError("cannot delete the active persona")
        await self.session.delete(row)
        return True


# ---------------------------------------------------------------------------
# F4 — Run spans
# ---------------------------------------------------------------------------


class RunSpanRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def insert(self, row: RunSpanRow) -> RunSpanRow:
        self.session.add(row)
        await self.session.flush()
        return row

    async def list_for_run(self, run_id: str) -> list[RunSpanRow]:
        stmt = (
            select(RunSpanRow)
            .where(RunSpanRow.run_id == run_id)
            .order_by(RunSpanRow.started_at)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())


# ---------------------------------------------------------------------------
# F5 — Webhook triggers
# ---------------------------------------------------------------------------


class TriggerRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_all(self) -> list[TriggerRow]:
        result = await self.session.execute(select(TriggerRow))
        return list(result.scalars().all())

    async def get(self, trigger_id: str) -> TriggerRow | None:
        return await self.session.get(TriggerRow, trigger_id)

    async def create(self, row: TriggerRow) -> TriggerRow:
        self.session.add(row)
        return row

    async def mark_fired(self, trigger_id: str) -> None:
        row = await self.session.get(TriggerRow, trigger_id)
        if row is None:
            return
        row.last_fired_at = _now()
        row.fires_total += 1

    async def delete(self, trigger_id: str) -> bool:
        row = await self.session.get(TriggerRow, trigger_id)
        if row is None:
            return False
        await self.session.delete(row)
        return True


# ---------------------------------------------------------------------------
# F6 — Annotations + incident acks
# ---------------------------------------------------------------------------


class AnnotationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_for(self, *, kind: str, target_id: str) -> list[AnnotationRow]:
        stmt = (
            select(AnnotationRow)
            .where((AnnotationRow.kind == kind) & (AnnotationRow.target_id == target_id))
            .order_by(AnnotationRow.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def append(self, row: AnnotationRow) -> AnnotationRow:
        self.session.add(row)
        return row

    async def delete(self, annotation_id: int) -> bool:
        row = await self.session.get(AnnotationRow, annotation_id)
        if row is None:
            return False
        await self.session.delete(row)
        return True


class IncidentAckRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ack(self, *, audit_id: int, acked_by: str, note: str | None) -> IncidentAckRow:
        existing = await self.session.get(IncidentAckRow, audit_id)
        if existing is not None:
            return existing
        row = IncidentAckRow(audit_id=audit_id, acked_by=acked_by, note=note)
        self.session.add(row)
        return row

    async def is_acked(self, audit_id: int) -> bool:
        row = await self.session.get(IncidentAckRow, audit_id)
        return row is not None
