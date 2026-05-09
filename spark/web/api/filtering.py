"""Filtering page — operator surface over the data-class guardrails.

Endpoints:

* ``GET  /api/filtering/policy`` — full snapshot for the page
  (categories grouped by family, current global + agent overrides,
  per-detector catalog, mask style defaults).
* ``PUT  /api/filtering/policy/category/{data_class}`` — update one
  category's level / scopes / mask_style / min_confidence /
  require_consensus.
* ``PUT  /api/filtering/policy/agent/{agent_name}/{data_class}`` —
  same, but scoped to one agent.
* ``DELETE /api/filtering/policy/agent/{agent_name}/{data_class}`` —
  clear an agent override and fall back to the global / default.
* ``PUT  /api/filtering/policy/category/{data_class}/detector/{rule_id}``
  — toggle a single detector inside a category (Advanced drawer).
* ``POST /api/filtering/dry-run`` — apply the resolved policy to an
  arbitrary string + agent + scope and return the hits + redacted
  output. No DB writes; an info-severity audit row records that the
  sandbox was used so noisy operators show up.

Every mutation writes a ``security.filtering.<…>`` audit row at
elevated severity, mirroring the ``security.data_class.*`` precedent.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from spark.config.enums import DataClass, DataClassLevel, DataScope, MaskStyle
from spark.persistence.db import session_scope
from spark.persistence.learning_repos import (
    AuditRepository,
    DataPolicyRepository,
)
from spark.privacy.detector_catalog import DETECTOR_CATALOG, FAMILIES
from spark.privacy.guardrails import (
    BUILTIN_DEFAULTS,
    apply_guardrails,
    bump_policy_version,
    get_resolved_policy,
)
from spark.privacy.mask import (
    DEFAULT_MASK_STYLE,
    PREVIEW_SAMPLES,
    render_mask,
)
from spark.utils.time import isoformat as iso_utc
from spark.web.auth import Principal, require_admin, require_viewer

router = APIRouter()


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CategoryPatch(BaseModel):
    """Per-category settings edited from the Filtering page card."""

    model_config = ConfigDict(extra="forbid")

    level: str = Field(max_length=16)
    scopes: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(default="", max_length=1000)
    mask_style: str | None = Field(default=None, max_length=32)
    min_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    require_consensus: bool | None = None

    @field_validator("level")
    @classmethod
    def _check_level(cls, v: str) -> str:
        try:
            DataClassLevel(v)
        except ValueError as exc:
            raise ValueError(
                "level must be one of: allow, warn, redact, shadow_block, block"
            ) from exc
        return v

    @field_validator("scopes")
    @classmethod
    def _check_scopes(cls, vs: list[str]) -> list[str]:
        for v in vs:
            try:
                DataScope(v)
            except ValueError as exc:
                raise ValueError(f"unknown scope {v!r}") from exc
        return vs

    @field_validator("mask_style")
    @classmethod
    def _check_mask_style(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            MaskStyle(v)
        except ValueError as exc:
            raise ValueError(f"unknown mask_style {v!r}") from exc
        return v


class DetectorPatch(BaseModel):
    """Toggle / threshold for one detector inside a category."""

    model_config = ConfigDict(extra="forbid")
    enabled: bool | None = None
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)


class DryRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=8000)
    agent_name: str | None = Field(default=None, max_length=128)
    scope: str = Field(default="model_output", max_length=32)

    @field_validator("scope")
    @classmethod
    def _check_scope(cls, v: str) -> str:
        try:
            DataScope(v)
        except ValueError as exc:
            raise ValueError(f"unknown scope {v!r}") from exc
        return v


# ---------------------------------------------------------------------------
# GET: full snapshot
# ---------------------------------------------------------------------------


def _row_to_category_view(row: Any) -> dict[str, Any]:
    return {
        "id": row.id,
        "scope_kind": row.scope_kind,
        "agent_name": row.agent_name,
        "data_class": row.data_class,
        "level": row.level,
        "scopes": sorted(filter(None, (row.scopes or "").split(","))),
        "reason": row.reason,
        "mask_style": row.mask_style,
        "min_confidence": row.min_confidence,
        "require_consensus": row.require_consensus,
        "detector_overrides": _safe_loads(row.detector_overrides_json),
        "updated_at": iso_utc(row.updated_at) if row.updated_at else None,
        "updated_by": row.updated_by,
    }


def _safe_loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


@router.get("/policy")
async def get_policy(
    _: Principal = Depends(require_viewer),
) -> dict[str, Any]:
    """Snapshot of every category, every override, and the detector catalog.

    The frontend renders this in one render pass — no chained calls.
    """
    async with session_scope() as session:
        rows = await DataPolicyRepository(session).list_all()

    globals_by_class: dict[str, dict[str, Any]] = {}
    agents_by_agent: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        view = _row_to_category_view(row)
        if row.scope_kind == "global":
            globals_by_class[row.data_class] = view
        elif row.scope_kind == "agent" and row.agent_name is not None:
            agents_by_agent.setdefault(row.agent_name, {})[row.data_class] = view

    categories: list[dict[str, Any]] = []
    for cls, default in BUILTIN_DEFAULTS.items():
        catalog = DETECTOR_CATALOG.get(cls, [])
        categories.append(
            {
                "data_class": cls.value,
                "family": _family_for(cls.value),
                "description": default.description,
                "default_level": default.level.value,
                "default_scopes": sorted(s.value for s in default.scopes),
                "default_mask_style": DEFAULT_MASK_STYLE.get(
                    cls, MaskStyle.PLACEHOLDER_CLASS
                ).value,
                "default_min_confidence": default.min_confidence,
                "default_require_consensus": default.require_consensus,
                "global_override": globals_by_class.get(cls.value),
                "detectors": [
                    {
                        "rule_id": d.rule_id,
                        "label": d.label,
                        "description": d.description,
                        "tier": d.tier,
                    }
                    for d in catalog
                ],
            }
        )

    families = [
        {"id": fid, "label": label, "members": [c.value for c in members]}
        for fid, label, members in FAMILIES
    ]
    mask_styles = [
        {
            "value": style.value,
            "label": _mask_style_label(style),
            "samples": {
                cls.value: render_mask(sample, style=style, data_class=cls)
                for cls, sample in PREVIEW_SAMPLES.items()
            },
        }
        for style in MaskStyle
    ]
    return {
        "families": families,
        "categories": categories,
        "agent_overrides": agents_by_agent,
        "mask_styles": mask_styles,
    }


def _family_for(data_class_value: str) -> str:
    for fid, _, members in FAMILIES:
        for m in members:
            if m.value == data_class_value:
                return fid
    return "other"


def _mask_style_label(style: MaskStyle) -> str:
    return {
        MaskStyle.PLACEHOLDER_CLASS: "[REDACTED:class] — class-tagged placeholder",
        MaskStyle.PLACEHOLDER_PLAIN: "[REDACTED] — plain placeholder",
        MaskStyle.LAST_4: "Reveal last 4",
        MaskStyle.FIRST_4: "Reveal first 4",
        MaskStyle.INITIAL: "Initials only",
        MaskStyle.HASH_SHORT: "Deterministic 8-char hash",
        MaskStyle.STRIP: "Strip entirely",
    }[style]


# ---------------------------------------------------------------------------
# PUT: category (global)
# ---------------------------------------------------------------------------


def _serialize_overrides(d: dict[str, Any] | None) -> str | None:
    if not d:
        return None
    return json.dumps(d, sort_keys=True)


@router.put("/policy/category/{data_class}")
async def put_global_category(
    data_class: str,
    body: CategoryPatch,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    try:
        DataClass(data_class)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown data class") from exc

    async with session_scope() as session:
        repo = DataPolicyRepository(session)
        await repo.upsert_global(
            data_class=data_class,
            level=body.level,
            scopes=",".join(body.scopes),
            reason=body.reason,
            actor=principal.subject,
            mask_style=body.mask_style,
            min_confidence=body.min_confidence,
            require_consensus=body.require_consensus,
        )
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.filtering.category.update",
            target=f"global:{data_class}",
            diff={
                "level": body.level,
                "scopes": body.scopes,
                "mask_style": body.mask_style,
                "min_confidence": body.min_confidence,
                "require_consensus": body.require_consensus,
            },
            reason=body.reason,
            severity="elevated",
        )
    bump_policy_version()
    return {"ok": True}


@router.put("/policy/agent/{agent_name}/{data_class}")
async def put_agent_category(
    agent_name: str,
    data_class: str,
    body: CategoryPatch,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    try:
        DataClass(data_class)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown data class") from exc

    async with session_scope() as session:
        repo = DataPolicyRepository(session)
        await repo.upsert_agent(
            agent_name=agent_name,
            data_class=data_class,
            level=body.level,
            scopes=",".join(body.scopes),
            reason=body.reason,
            actor=principal.subject,
            mask_style=body.mask_style,
            min_confidence=body.min_confidence,
            require_consensus=body.require_consensus,
        )
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.filtering.category.update",
            target=f"agent:{agent_name}:{data_class}",
            diff={
                "level": body.level,
                "scopes": body.scopes,
                "mask_style": body.mask_style,
                "min_confidence": body.min_confidence,
                "require_consensus": body.require_consensus,
            },
            reason=body.reason,
            severity="elevated",
        )
    bump_policy_version()
    return {"ok": True}


@router.delete("/policy/agent/{agent_name}/{data_class}")
async def delete_agent_category(
    agent_name: str,
    data_class: str,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    async with session_scope() as session:
        repo = DataPolicyRepository(session)
        deleted = await repo.delete_agent(agent_name, data_class)
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.filtering.category.revert",
            target=f"agent:{agent_name}:{data_class}",
            diff={"deleted": deleted},
            severity="elevated",
        )
    bump_policy_version()
    return {"ok": True, "deleted": deleted}


# ---------------------------------------------------------------------------
# PUT: detector toggle/threshold
# ---------------------------------------------------------------------------


@router.put("/policy/category/{data_class}/detector/{rule_id}")
async def put_detector_override(
    data_class: str,
    rule_id: str,
    body: DetectorPatch,
    principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    """Merge a single detector toggle/threshold into the global category row.

    The merged map lives in ``detector_overrides_json``; sending
    ``{enabled: null, threshold: null}`` clears the entry entirely.
    """
    try:
        DataClass(data_class)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="unknown data class") from exc

    catalog = DETECTOR_CATALOG.get(DataClass(data_class), [])
    if not any(d.rule_id == rule_id for d in catalog):
        raise HTTPException(
            status_code=400, detail=f"unknown detector rule_id {rule_id!r}"
        )

    async with session_scope() as session:
        repo = DataPolicyRepository(session)
        existing = await repo.get_global(data_class)
        overrides = _safe_loads(existing.detector_overrides_json) if existing else {}

        entry: dict[str, Any] = dict(overrides.get(rule_id, {}))
        if body.enabled is not None:
            entry["enabled"] = body.enabled
        elif "enabled" in entry and body.enabled is None:
            # Clear with explicit null when sent.
            pass
        if body.threshold is not None:
            entry["threshold"] = body.threshold

        # If the request explicitly sends both fields as null, drop the
        # entry. Pydantic gives us None for unset, so we can't tell the
        # two apart — safer to leave entries until the operator clears
        # them via a full PUT on the category.
        if entry:
            overrides[rule_id] = entry
        else:
            overrides.pop(rule_id, None)

        # Preserve other category fields if a row already exists; if not,
        # seed with the built-in defaults so the row is internally
        # consistent.
        cls_enum = DataClass(data_class)
        default = BUILTIN_DEFAULTS[cls_enum]
        await repo.upsert_global(
            data_class=data_class,
            level=existing.level if existing else default.level.value,
            scopes=existing.scopes
            if existing
            else ",".join(sorted(s.value for s in default.scopes)),
            reason=existing.reason if existing else "",
            actor=principal.subject,
            mask_style=existing.mask_style if existing else None,
            min_confidence=existing.min_confidence if existing else None,
            require_consensus=existing.require_consensus if existing else None,
            detector_overrides_json=_serialize_overrides(overrides),
        )
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.filtering.detector.update",
            target=f"global:{data_class}:{rule_id}",
            diff={"enabled": body.enabled, "threshold": body.threshold},
            severity="elevated",
        )
    bump_policy_version()
    return {"ok": True, "rule_id": rule_id, "entry": overrides.get(rule_id)}


# ---------------------------------------------------------------------------
# POST: dry-run sandbox
# ---------------------------------------------------------------------------


@router.post("/dry-run")
async def dry_run(
    body: DryRunRequest,
    principal: Principal = Depends(require_viewer),
) -> dict[str, Any]:
    """Apply guardrails to ``body.text`` without persisting anything.

    Returns the unredacted-vs-redacted text, the per-class hit list,
    and the resolved policy snapshot the operator's edits would
    produce. Used by the sandbox at the bottom of the Filtering page.
    """
    scope = DataScope(body.scope)
    try:
        outcome = await apply_guardrails(
            body.text, agent_name=body.agent_name, scope=scope
        )
    except Exception as exc:  # block raises here — surface as a 200 dry-run.
        # Importing locally to keep the route lean.
        from spark.errors.codes import SparkError  # noqa: PLC0415

        if isinstance(exc, SparkError):
            return {
                "blocked": True,
                "error_code": exc.code.value,
                "message": exc.args[0] if exc.args else str(exc),
                "detail": getattr(exc, "detail", {}),
                "input": body.text,
                "output": None,
                "hits": [],
            }
        raise

    resolved = await get_resolved_policy(agent_name=body.agent_name, scope=scope)

    hits = [
        {
            "data_class": h.data_class.value,
            "start": h.start,
            "end": h.end,
            "matched": body.text[h.start : h.end],
            "rule_id": h.rule_id,
            "tier": h.tier,
            "confidence": h.confidence,
        }
        for h in outcome.hits
    ]
    levels_applied = [
        {"data_class": cls.value, "level": lvl.value}
        for cls, lvl in outcome.levels_applied
    ]
    policy_snapshot = {
        cls.value: {
            "level": pol.level.value,
            "source": pol.source,
            "mask_style": pol.mask_style.value,
            "min_confidence": pol.min_confidence,
            "require_consensus": pol.require_consensus,
            "scopes": sorted(s.value for s in pol.scopes),
        }
        for cls, pol in resolved.items()
    }

    # Audit the dry-run at info severity so operators leave a paper
    # trail without bloating elevated audit. Body content is summarized
    # as just hits + counts, never the raw input.
    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=principal.subject,
            kind="security.filtering.dry_run",
            target=f"agent:{body.agent_name or '__none__'}",
            diff={
                "scope": scope.value,
                "input_chars": len(body.text),
                "hit_classes": sorted({h["data_class"] for h in hits}),
                "rule_ids": sorted({h["rule_id"] for h in hits}),
            },
            severity="info",
        )

    return {
        "blocked": False,
        "input": body.text,
        "output": outcome.text,
        "hits": hits,
        "levels_applied": levels_applied,
        "policy_snapshot": policy_snapshot,
    }
