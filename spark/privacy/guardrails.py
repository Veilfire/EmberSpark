"""Data Classification Guardrails — the policy resolver + enforcement.

Public entry points:

* :data:`BUILTIN_DEFAULTS` — factory default level + scopes per class.
* :func:`resolve_policy` — pure function: given ``(agent, policy_rows,
  grants)`` return an effective ``{class: (level, scopes)}`` map.
* :func:`apply_guardrails` — runs the classifiers for the enabled
  classes, applies the worst-per-hit level, returns the (possibly
  redacted) text plus a structured hit summary, and raises
  :class:`~spark.errors.codes.SparkError` with
  ``DATA_CLASS_BLOCKED`` on any ``block`` hit.

The resolver's output is cached in-process keyed by
``(agent_name, scope, policy_version)`` — the version counter is bumped
by the REST routes on every mutation so stale caches clear without a
restart.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from spark.config.enums import DataClass, DataClassLevel, DataScope, MaskStyle
from spark.errors.codes import ErrorCode, SparkError
from spark.persistence.db import session_scope
from spark.persistence.learning_models import (
    DataClassGrantRow,
    DataClassPolicyRow,
)
from spark.persistence.learning_repos import (
    DataGrantRepository,
    DataPolicyRepository,
)
from spark.privacy.classifiers import DetectorHit, run_classifiers
from spark.privacy.mask import default_for as _default_mask_for, render_mask
from spark.utils.time import utcnow


# ---------------------------------------------------------------------------
# Built-in defaults
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ClassDefault:
    """Factory defaults for one data class.

    ``require_consensus``: when true, a single detector (tier1 OR tier2)
    is not enough to fire — both must agree on the same span. Use for
    noisy classes where a single signal has a high false-positive rate
    (e.g. ``pii.name`` via Presidio PERSON).

    ``min_confidence``: a floor applied after the fusion step. Hits
    below the floor are dropped, never escalated.
    """

    level: DataClassLevel
    scopes: frozenset[DataScope]
    description: str
    require_consensus: bool = False
    min_confidence: float = 0.5


# Every scope applies by default. Operators narrow by editing the
# global policy row's scope set.
_ALL_SCOPES: frozenset[DataScope] = frozenset(DataScope)
# CLI classes: dangerous argv is the target. Model *describing* `sudo`
# or `rm -rf` in prose is not dangerous — it's the agent executing
# those shapes that is. So default scopes exclude ``model_output``.
_CLI_SCOPES: frozenset[DataScope] = frozenset(
    {DataScope.SHELL_ARGS, DataScope.USER_INPUT}
)

BUILTIN_DEFAULTS: dict[DataClass, ClassDefault] = {
    DataClass.PII_BASIC: ClassDefault(
        DataClassLevel.REDACT, _ALL_SCOPES,
        "Email, phone, address. Redact by default — too common to block.",
        min_confidence=0.55,
    ),
    DataClass.PII_NAME: ClassDefault(
        DataClassLevel.ALLOW, _ALL_SCOPES,
        "Person names (Presidio). Allow by default — high false-positive rate.",
        # Require consensus ensures we only fire when two signals agree.
        # Today pii.name is Presidio-only so this effectively suppresses
        # it unless operator disables the knob. That's the whole point:
        # the default is "detect but don't disturb workflows".
        require_consensus=True,
        min_confidence=0.7,
    ),
    DataClass.PII_GOV_ID: ClassDefault(
        DataClassLevel.BLOCK, _ALL_SCOPES,
        "SSN, passport, driver license, ITIN. Should never leak unauthorized.",
        min_confidence=0.7,
    ),
    DataClass.PII_MEDICAL: ClassDefault(
        DataClassLevel.BLOCK, _ALL_SCOPES,
        "Medical license, ICD-10 codes, diagnosis terms. HIPAA-adjacent.",
        min_confidence=0.6,
    ),
    DataClass.FINANCIAL_CARD: ClassDefault(
        DataClassLevel.BLOCK, _ALL_SCOPES,
        "Luhn-validated PAN. PCI — blocked unless an agent has an explicit grant.",
        min_confidence=0.8,
    ),
    DataClass.FINANCIAL_BANK: ClassDefault(
        DataClassLevel.BLOCK, _ALL_SCOPES,
        "IBAN, routing, SWIFT/BIC.",
        min_confidence=0.75,
    ),
    DataClass.FINANCIAL_CRYPTO: ClassDefault(
        DataClassLevel.REDACT, _ALL_SCOPES,
        "Wallet addresses and seed phrases.",
        min_confidence=0.55,
    ),
    DataClass.CREDENTIALS_API: ClassDefault(
        DataClassLevel.REDACT, _ALL_SCOPES,
        "API keys, bearer tokens, JWTs — redacted from tool output and logs.",
        min_confidence=0.6,
    ),
    DataClass.CREDENTIALS_PEM: ClassDefault(
        DataClassLevel.BLOCK, _ALL_SCOPES,
        "PEM-encoded private keys.",
    ),
    DataClass.SECRETS_VAULT: ClassDefault(
        DataClassLevel.REDACT, _ALL_SCOPES,
        "Any exact match against a value in the secrets vault.",
    ),
    DataClass.CLI_DESTRUCTIVE: ClassDefault(
        DataClassLevel.BLOCK, _CLI_SCOPES,
        "rm -rf /, dd, mkfs, shred, fork-bomb. Blocked only where it matters: the argv and user input.",
    ),
    DataClass.CLI_PRIVILEGE: ClassDefault(
        DataClassLevel.BLOCK, _CLI_SCOPES,
        "sudo, su, doas, chmod 777, setuid bits.",
    ),
    DataClass.CLI_PIPE_EXEC: ClassDefault(
        DataClassLevel.BLOCK, _CLI_SCOPES,
        "curl|sh, wget|bash, iwr|iex. Almost never legitimate in argv.",
    ),
    DataClass.CLI_EXFILTRATION: ClassDefault(
        DataClassLevel.WARN, _CLI_SCOPES,
        "nc, scp, ssh to external hosts. Warn — high false-positive rate.",
    ),
    DataClass.PROMPT_INJECTION: ClassDefault(
        DataClassLevel.WARN,
        frozenset(
            {DataScope.USER_INPUT, DataScope.TOOL_OUTPUT, DataScope.MODEL_OUTPUT}
        ),
        "Ignore-prior-instructions markers and role-flip attempts.",
    ),
}


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ResolvedPolicy:
    """Effective policy for one ``(data_class, scope)`` pair.

    ``mask_style`` / ``min_confidence`` / ``require_consensus`` /
    ``detector_overrides`` are populated by the Filtering page and
    consumed inside ``apply_guardrails``. They use the same
    grant → agent → global → default precedence as ``level`` / ``scopes``.

    ``detector_overrides`` is a freeform mapping from detector
    ``rule_id`` to a small dict (``{enabled?: bool, threshold?: float}``).
    Unknown rule_ids are skipped silently.
    """

    level: DataClassLevel
    scopes: frozenset[DataScope]
    source: str  # "grant" | "agent" | "global" | "default"
    grant_id: int | None = None
    mask_style: MaskStyle = MaskStyle.PLACEHOLDER_CLASS
    min_confidence: float = 0.5
    require_consensus: bool = False
    detector_overrides: dict[str, dict[str, object]] = field(default_factory=dict)


def _parse_scopes(raw: str) -> frozenset[DataScope]:
    out: set[DataScope] = set()
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        try:
            out.add(DataScope(piece))
        except ValueError:
            continue
    return frozenset(out)


def _join_scopes(scopes: frozenset[DataScope]) -> str:
    return ",".join(sorted(s.value for s in scopes))


def _parse_mask_style(raw: str | None) -> MaskStyle | None:
    if not raw:
        return None
    try:
        return MaskStyle(raw)
    except ValueError:
        return None


def _parse_detector_overrides(raw: str | None) -> dict[str, dict[str, object]]:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    out: dict[str, dict[str, object]] = {}
    for key, value in parsed.items():
        if not isinstance(key, str):
            continue
        if isinstance(value, dict):
            out[key] = {str(k): v for k, v in value.items()}
        elif isinstance(value, bool):
            # Shorthand ``{rule_id: false}`` means ``{enabled: false}``.
            out[key] = {"enabled": value}
    return out


def resolve_policy(
    *,
    agent_name: str | None,
    policy_rows: list[DataClassPolicyRow],
    grants: list[DataClassGrantRow],
    scope: DataScope,
) -> dict[DataClass, ResolvedPolicy]:
    """Compute the effective policy per class for a given agent + scope.

    Resolution order: grant → agent → global → built-in default.
    """
    out: dict[DataClass, ResolvedPolicy] = {}

    globals_by_class: dict[DataClass, DataClassPolicyRow] = {}
    agent_by_class: dict[DataClass, DataClassPolicyRow] = {}
    for row in policy_rows:
        try:
            cls = DataClass(row.data_class)
        except ValueError:
            continue
        if row.scope_kind == "global":
            globals_by_class[cls] = row
        elif row.scope_kind == "agent" and row.agent_name == agent_name:
            agent_by_class[cls] = row

    grants_by_class: dict[DataClass, DataClassGrantRow] = {}
    for g in grants:
        try:
            cls = DataClass(g.data_class)
        except ValueError:
            continue
        scopes = _parse_scopes(g.scopes)
        if scope not in scopes:
            continue
        # Grant wins if it covers this scope. Multiple active grants for
        # the same class: prefer the most recently issued (highest id).
        prior = grants_by_class.get(cls)
        if prior is None or ((g.id or 0) > (prior.id or 0)):
            grants_by_class[cls] = g

    for cls, default in BUILTIN_DEFAULTS.items():
        # The knobs that fall back independently of level/source: a
        # global row can set the mask style while an agent row narrows
        # the scopes, and the agent row's missing mask_style should not
        # erase the global one.
        global_row = globals_by_class.get(cls)
        agent_row = agent_by_class.get(cls)

        global_mask = _parse_mask_style(global_row.mask_style) if global_row else None
        agent_mask = _parse_mask_style(agent_row.mask_style) if agent_row else None
        mask_style = (
            agent_mask
            if agent_mask is not None
            else global_mask
            if global_mask is not None
            else _default_mask_for(cls)
        )

        global_min = (
            global_row.min_confidence
            if global_row is not None and global_row.min_confidence is not None
            else None
        )
        agent_min = (
            agent_row.min_confidence
            if agent_row is not None and agent_row.min_confidence is not None
            else None
        )
        min_confidence = (
            agent_min
            if agent_min is not None
            else global_min
            if global_min is not None
            else default.min_confidence
        )

        if agent_row is not None and agent_row.require_consensus is not None:
            require_consensus = bool(agent_row.require_consensus)
        elif global_row is not None and global_row.require_consensus is not None:
            require_consensus = bool(global_row.require_consensus)
        else:
            require_consensus = default.require_consensus

        # Detector overrides are merged: global is the base, agent
        # values overlay it. A rule_id present in both takes the agent
        # value wholesale (no per-key merge — too easy to surprise).
        detector_overrides: dict[str, dict[str, object]] = {}
        if global_row is not None:
            detector_overrides.update(
                _parse_detector_overrides(global_row.detector_overrides_json)
            )
        if agent_row is not None:
            detector_overrides.update(
                _parse_detector_overrides(agent_row.detector_overrides_json)
            )

        # 1. Grant
        grant = grants_by_class.get(cls)
        if grant is not None:
            try:
                level = DataClassLevel(grant.level_override)
            except ValueError:
                level = DataClassLevel.ALLOW
            out[cls] = ResolvedPolicy(
                level=level,
                scopes=_parse_scopes(grant.scopes),
                source="grant",
                grant_id=grant.id,
                mask_style=mask_style,
                min_confidence=min_confidence,
                require_consensus=require_consensus,
                detector_overrides=detector_overrides,
            )
            continue

        # 2. Agent override
        if agent_row is not None:
            scopes = _parse_scopes(agent_row.scopes)
            if scope in scopes:
                try:
                    level = DataClassLevel(agent_row.level)
                except ValueError:
                    level = default.level
                out[cls] = ResolvedPolicy(
                    level=level,
                    scopes=scopes,
                    source="agent",
                    mask_style=mask_style,
                    min_confidence=min_confidence,
                    require_consensus=require_consensus,
                    detector_overrides=detector_overrides,
                )
                continue

        # 3. Global override
        if global_row is not None:
            scopes = _parse_scopes(global_row.scopes)
            if scope in scopes:
                try:
                    level = DataClassLevel(global_row.level)
                except ValueError:
                    level = default.level
                out[cls] = ResolvedPolicy(
                    level=level,
                    scopes=scopes,
                    source="global",
                    mask_style=mask_style,
                    min_confidence=min_confidence,
                    require_consensus=require_consensus,
                    detector_overrides=detector_overrides,
                )
                continue

        # 4. Built-in default — applies only if scope is covered.
        if scope in default.scopes:
            out[cls] = ResolvedPolicy(
                level=default.level,
                scopes=default.scopes,
                source="default",
                mask_style=mask_style,
                min_confidence=min_confidence,
                require_consensus=require_consensus,
                detector_overrides=detector_overrides,
            )
        else:
            out[cls] = ResolvedPolicy(
                level=DataClassLevel.ALLOW,
                scopes=default.scopes,
                source="default",
                mask_style=mask_style,
                min_confidence=min_confidence,
                require_consensus=require_consensus,
                detector_overrides=detector_overrides,
            )

    return out


# ---------------------------------------------------------------------------
# Policy loading
# ---------------------------------------------------------------------------
#
# No in-process cache: the resolver is cheap (15 rows + a handful of
# grants, pure dict lookups) and the DB is local SQLite. The caching
# layer that used to live here introduced a race between "writer
# committed" and "reader evicted its cached value" — for the size of
# the policy set, the cache wasn't worth the foot-gun.
#
# ``bump_policy_version`` is kept as a no-op for REST callers so the
# API pattern stays consistent, and so we can add back a cache later
# with proper coherency (e.g. PubSub invalidation) without touching
# the mutation call sites.


_policy_version = 0


def bump_policy_version() -> int:
    """No-op today; kept as an extension point for future cache coherency.

    API routes still call it to preserve the invalidation-on-write
    contract. When a shared cache is reintroduced, this is where it
    clears.
    """
    global _policy_version
    _policy_version += 1
    return _policy_version


async def _load_rows_and_grants(
    agent_name: str | None,
) -> tuple[list[DataClassPolicyRow], list[DataClassGrantRow]]:
    async with session_scope() as session:
        policy_repo = DataPolicyRepository(session)
        grant_repo = DataGrantRepository(session)
        rows = await policy_repo.list_all()
        if agent_name is None:
            grants = await grant_repo.list_active()
        else:
            grants = await grant_repo.active_for_agent(agent_name)
    return rows, grants


async def get_resolved_policy(
    *, agent_name: str | None, scope: DataScope
) -> dict[DataClass, ResolvedPolicy]:
    rows, grants = await _load_rows_and_grants(agent_name)
    return resolve_policy(
        agent_name=agent_name, policy_rows=rows, grants=grants, scope=scope
    )


# ---------------------------------------------------------------------------
# Enforcement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GuardrailOutcome:
    """Result of applying guardrails to a piece of text."""

    text: str
    hits: tuple[DetectorHit, ...]
    levels_applied: tuple[tuple[DataClass, DataClassLevel], ...]
    policy_sources: tuple[tuple[DataClass, str], ...]

    @property
    def any_redaction(self) -> bool:
        return any(l == DataClassLevel.REDACT for _, l in self.levels_applied)

    @property
    def any_warn(self) -> bool:
        return any(l == DataClassLevel.WARN for _, l in self.levels_applied)


_LEVEL_ORDER: dict[DataClassLevel, int] = {
    DataClassLevel.ALLOW: 0,
    DataClassLevel.WARN: 1,
    DataClassLevel.REDACT: 2,
    DataClassLevel.SHADOW_BLOCK: 3,
    DataClassLevel.BLOCK: 4,
}


def _level_worse(a: DataClassLevel, b: DataClassLevel) -> DataClassLevel:
    return a if _LEVEL_ORDER[a] >= _LEVEL_ORDER[b] else b


def _apply_redactions(
    text: str,
    hits: list[DetectorHit],
    *,
    mask_styles: dict[DataClass, MaskStyle] | None = None,
) -> str:
    """Replace each hit's span with its mask-style rendering.

    ``mask_styles`` lets the caller hand down the per-class style chosen
    by the resolver. When absent, falls back to the per-class default
    style — which itself reduces to the legacy ``[REDACTED:<class>]``
    placeholder for almost every category, so call sites that haven't
    been updated still produce the same output.
    """
    # Sort by start desc so span replacements don't shift earlier indices.
    ordered = sorted(hits, key=lambda h: h.start, reverse=True)
    out = text
    for h in ordered:
        style = (
            mask_styles.get(h.data_class)
            if mask_styles is not None
            else _default_mask_for(h.data_class)
        )
        if style is None:
            style = _default_mask_for(h.data_class)
        original = text[h.start : h.end]
        replacement = render_mask(original, style=style, data_class=h.data_class)
        out = out[: h.start] + replacement + out[h.end :]
    return out


def _detector_enabled(rule_id: str, overrides: dict[str, dict[str, object]]) -> bool:
    """Return False if an override explicitly disables this detector.

    ``rule_id`` may be a fused id like ``aws-access-key+presidio:CREDIT_CARD``;
    each component is checked. Any component flagged ``enabled=False``
    drops the whole hit. Unknown rule_ids are silently allowed.
    """
    if not overrides:
        return True
    for component in rule_id.split("+"):
        cfg = overrides.get(component)
        if not cfg:
            continue
        if cfg.get("enabled") is False:
            return False
    return True


def _spans_overlap(a: DetectorHit, b: DetectorHit) -> bool:
    return a.start < b.end and b.start < a.end


def fuse_hits(raw_hits: list[DetectorHit]) -> list[DetectorHit]:
    """Collapse overlapping hits of the same class into a canonical hit.

    When tier-1 (deterministic / checksummed) and tier-2 (statistical /
    NER) detectors both fire on overlapping spans of the same class,
    that's *consensus* — strong signal. We keep one hit, prefer the
    tier-1 span (it's typically tighter), and boost the confidence by
    ``+0.10`` (capped at 1.0). The surviving hit's ``rule_id`` records
    both sources so audit shows which detectors agreed.

    When only a single tier fires on a span, the hit passes through
    unchanged. Downstream ``require_consensus`` and ``min_confidence``
    gates then decide whether the hit actually matters.
    """
    if not raw_hits:
        return []
    # Bucket by class; within a class, greedily merge overlapping spans.
    by_class: dict[DataClass, list[DetectorHit]] = {}
    for h in raw_hits:
        by_class.setdefault(h.data_class, []).append(h)

    fused: list[DetectorHit] = []
    for cls, cls_hits in by_class.items():
        # Sort by start so overlap scan is linear.
        cls_hits.sort(key=lambda h: (h.start, h.end))
        clusters: list[list[DetectorHit]] = []
        for h in cls_hits:
            if clusters and _spans_overlap(clusters[-1][-1], h):
                clusters[-1].append(h)
            else:
                clusters.append([h])

        for cluster in clusters:
            tiers = {h.tier for h in cluster}
            consensus = "tier1" in tiers and "tier2" in tiers
            # Prefer tier1 hits as the canonical span (tighter, checksummed).
            # If no tier1, use the highest-confidence tier2.
            tier1_hits = [h for h in cluster if h.tier == "tier1"]
            primary = (
                max(tier1_hits, key=lambda h: h.confidence)
                if tier1_hits
                else max(cluster, key=lambda h: h.confidence)
            )
            boosted_conf = primary.confidence
            rule_id = primary.rule_id
            if consensus:
                boosted_conf = min(1.0, primary.confidence + 0.10)
                secondary = next(
                    (h for h in cluster if h.tier != primary.tier),
                    None,
                )
                if secondary is not None:
                    rule_id = f"{primary.rule_id}+{secondary.rule_id}"
            fused.append(
                DetectorHit(
                    data_class=cls,
                    start=primary.start,
                    end=primary.end,
                    confidence=boosted_conf,
                    rule_id=rule_id,
                    redaction=primary.redaction,
                    tier="consensus" if consensus else primary.tier,
                )
            )
    return fused


async def apply_guardrails(
    text: str,
    *,
    agent_name: str | None,
    scope: DataScope,
) -> GuardrailOutcome:
    """Scan ``text`` against the resolved policy and enforce.

    Pipeline:

    1. Resolve the policy map for ``(agent, scope)``.
    2. Run the classifiers whose class is non-allow at this scope.
    3. Fuse overlapping hits → consensus boost when tier1+tier2 agree.
    4. Apply per-class ``min_confidence`` threshold.
    5. Apply per-class ``require_consensus`` gate.
    6. Compute worst level per class.
    7. On any ``block``: raise :class:`SparkError`.
    8. On ``shadow_block``: audit as-if, but pass through.
    9. On ``redact``: replace spans in place.
    """
    if not text:
        return GuardrailOutcome(text=text, hits=(), levels_applied=(), policy_sources=())

    resolved = await get_resolved_policy(agent_name=agent_name, scope=scope)
    # Which classes have anything non-allow at this scope?
    enabled: set[DataClass] = {
        cls
        for cls, pol in resolved.items()
        if pol.level is not DataClassLevel.ALLOW
    }
    if not enabled:
        return GuardrailOutcome(text=text, hits=(), levels_applied=(), policy_sources=())

    raw = run_classifiers(text, enabled_classes=frozenset(enabled))
    hits = fuse_hits(raw)
    if not hits:
        return GuardrailOutcome(text=text, hits=(), levels_applied=(), policy_sources=())

    # Apply per-class confidence + consensus gates from the resolved
    # policy (operator-tunable on the Filtering page) and drop hits
    # whose detector is explicitly disabled in the per-detector
    # overrides.
    accepted: list[DetectorHit] = []
    for hit in hits:
        pol = resolved.get(hit.data_class)
        min_conf = pol.min_confidence if pol is not None else 0.5
        require_consensus = pol.require_consensus if pol is not None else False
        overrides = pol.detector_overrides if pol is not None else {}

        if hit.confidence < min_conf:
            continue
        if require_consensus and hit.tier != "consensus":
            continue
        if not _detector_enabled(hit.rule_id, overrides):
            continue
        accepted.append(hit)

    if not accepted:
        return GuardrailOutcome(text=text, hits=(), levels_applied=(), policy_sources=())

    # Worst level seen per class (for telemetry + redaction decisions).
    per_class: dict[DataClass, DataClassLevel] = {}
    for hit in accepted:
        pol = resolved.get(hit.data_class)
        if pol is None:
            continue
        lvl = pol.level
        per_class[hit.data_class] = (
            _level_worse(per_class[hit.data_class], lvl)
            if hit.data_class in per_class
            else lvl
        )

    # If ANY class resolved to block, fail fast.
    block_classes = [
        cls for cls, lvl in per_class.items() if lvl is DataClassLevel.BLOCK
    ]
    if block_classes:
        detail: dict[str, Any] = {
            "classes": [c.value for c in block_classes],
            "scope": scope.value,
            "agent": agent_name,
            "matched_rule_ids": sorted(
                {h.rule_id for h in accepted if h.data_class in block_classes}
            ),
            "consensus_count": sum(
                1 for h in accepted if h.tier == "consensus"
                and h.data_class in block_classes
            ),
            "suggest_grant": True,
        }
        try:
            await _notify_block(
                agent_name=agent_name,
                scope=scope,
                classes=[c.value for c in block_classes],
                rule_ids=sorted(detail["matched_rule_ids"]),
            )
        except Exception:  # pragma: no cover
            pass
        raise SparkError(
            ErrorCode.DATA_CLASS_BLOCKED,
            f"Data class {block_classes[0].value} is blocked at scope {scope.value}",
            detail=detail,
        )

    # Shadow-block: audit as-if blocked but pass through. Useful as a
    # calibration tool before committing to block.
    shadow_classes = [
        cls for cls, lvl in per_class.items() if lvl is DataClassLevel.SHADOW_BLOCK
    ]
    if shadow_classes:
        try:
            await _audit_shadow(
                agent_name=agent_name,
                scope=scope,
                classes=[c.value for c in shadow_classes],
                rule_ids=sorted(
                    {h.rule_id for h in accepted if h.data_class in shadow_classes}
                ),
            )
        except Exception:  # pragma: no cover
            pass

    # Apply redactions for all REDACT classes inline. SHADOW_BLOCK also
    # passes through — no content modification — so it doesn't need a
    # span replacement.
    redact_hits = [
        h for h in accepted if per_class.get(h.data_class) is DataClassLevel.REDACT
    ]
    mask_styles = {
        cls: pol.mask_style for cls, pol in resolved.items() if pol is not None
    }
    redacted_text = (
        _apply_redactions(text, redact_hits, mask_styles=mask_styles)
        if redact_hits
        else text
    )

    levels_applied = tuple(sorted(per_class.items(), key=lambda kv: kv[0].value))
    policy_sources = tuple(
        (cls, resolved[cls].source) for cls in sorted(per_class, key=lambda c: c.value)
    )
    return GuardrailOutcome(
        text=redacted_text,
        hits=tuple(accepted),
        levels_applied=levels_applied,
        policy_sources=policy_sources,
    )


_NOTIFY_WINDOW_SECONDS = 300.0  # 5 minutes
_last_notified: dict[tuple[str, str, str], float] = {}


def _should_notify(key: tuple[str, str, str]) -> bool:
    """Simple in-process rolling-window dedup for block notifications.

    A given ``(agent, class, scope)`` tuple notifies at most once per
    ``_NOTIFY_WINDOW_SECONDS``. Without this, a spammy agent hitting
    the same guardrail repeatedly floods the bell.
    """
    import time  # noqa: PLC0415

    now = time.monotonic()
    last = _last_notified.get(key)
    if last is not None and (now - last) < _NOTIFY_WINDOW_SECONDS:
        return False
    _last_notified[key] = now
    return True


async def _notify_block(
    *,
    agent_name: str | None,
    scope: DataScope,
    classes: list[str],
    rule_ids: list[str],
) -> None:
    """Fire a ``DATA_CLASS_BLOCKED`` notification. Import is lazy so the
    guardrail module doesn't force-import the notification subsystem at
    CLI / test boot."""
    key = (agent_name or "global", classes[0], scope.value)
    if not _should_notify(key):
        return
    try:
        from spark.notifications.kinds import NotificationKind  # noqa: PLC0415
        from spark.notifications.service import NotificationService  # noqa: PLC0415
    except Exception:
        return

    service = NotificationService()
    title = f"Data class blocked: {classes[0]}"
    if len(classes) > 1:
        title += f" (+{len(classes) - 1} more)"
    body = (
        f"Agent {agent_name or '(global)'} hit a `block` policy at scope "
        f"`{scope.value}`. Matched rules: {', '.join(rule_ids[:5])}"
        f"{'…' if len(rule_ids) > 5 else ''}. Review in "
        "Security Center → Data Classes."
    )
    try:
        await service.notify(
            kind=NotificationKind.DATA_CLASS_BLOCKED,
            title=title,
            body=body,
            severity="elevated",
            target_kind="data_class",
            target_id=f"{agent_name or 'global'}:{classes[0]}",
            action_url="/security?tab=data-classes",
        )
    except Exception:  # pragma: no cover
        pass


async def _audit_shadow(
    *,
    agent_name: str | None,
    scope: DataScope,
    classes: list[str],
    rule_ids: list[str],
) -> None:
    """Record a shadow-block decision in the audit log without enforcing.

    Used when a class resolved to :attr:`DataClassLevel.SHADOW_BLOCK`.
    Operators use the resulting audit rows to calibrate FP rates before
    flipping the class to real ``block``.
    """
    try:
        from spark.persistence.db import session_scope  # noqa: PLC0415
        from spark.persistence.learning_repos import (  # noqa: PLC0415
            AuditRepository,
        )
    except Exception:
        return
    try:
        async with session_scope() as session:
            await AuditRepository(session).append(
                actor="system",
                kind="security.data_class.shadow_block",
                target=f"{agent_name or 'global'}:{classes[0]}",
                diff={
                    "classes": classes,
                    "scope": scope.value,
                    "matched_rule_ids": rule_ids,
                    "would_have_blocked": True,
                },
                reason="shadow-block calibration signal",
                severity="info",
            )
    except Exception:  # pragma: no cover
        pass


def apply_guardrails_sync(
    text: str,
    *,
    agent_name: str | None,
    scope: DataScope,
) -> GuardrailOutcome:
    """Synchronous wrapper — for plugin hot paths that run outside async.

    Uses ``asyncio.run`` only when there is no running loop; otherwise
    schedules via ``loop.run_until_complete``. Most Spark code paths
    already run under an event loop and should use
    :func:`apply_guardrails` directly.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(apply_guardrails(text, agent_name=agent_name, scope=scope))
    # We're in an async context — caller should use the async version.
    # Run a coroutine inline by spawning + waiting is not possible here
    # without deadlock risk. Raise loudly so the misuse is caught.
    raise RuntimeError(
        "apply_guardrails_sync called from inside a running event loop; "
        "use the async apply_guardrails() instead"
    )
