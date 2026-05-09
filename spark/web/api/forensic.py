"""Forensic review routes — admin-gated.

Every mutation + read on this router is audited.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from spark.forensic.reader import ForensicReadError, ForensicRepository
from spark.forensic.retention import run_retention_sweep
from spark.logging import get_logger
from spark.persistence.db import session_scope
from spark.persistence.learning_repos import AuditRepository
from spark.runtime.bootstrap import get_secret_manager
from spark.web.auth import Principal, require_admin

router = APIRouter()
log = get_logger("spark.forensic.api")


def _repo() -> ForensicRepository:
    return ForensicRepository(get_secret_manager())


async def _audit(
    *,
    actor: str,
    kind: str,
    target: str,
    severity: str = "info",
    reason: str = "",
    diff: dict | None = None,
) -> None:
    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=actor,
            kind=kind,
            target=target,
            severity=severity,
            reason=reason,
            diff=diff,
        )


@router.get("/")
async def list_captures(
    principal: Principal = Depends(require_admin),
) -> list[dict]:
    captures = await _repo().list_captures()
    await _audit(
        actor=f"user:{principal.subject}",
        kind="forensic.list",
        target="forensic_captures",
    )
    return captures


@router.get("/{run_id}")
async def get_capture(
    run_id: str,
    principal: Principal = Depends(require_admin),
) -> dict:
    capture = await _repo().get_capture(run_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")
    await _audit(
        actor=f"user:{principal.subject}",
        kind="forensic.read_metadata",
        target=run_id,
    )
    return capture


@router.get("/{run_id}/snapshots")
async def get_snapshots(
    run_id: str,
    principal: Principal = Depends(require_admin),
) -> dict:
    """Return the full decrypted snapshot chain for a run."""
    repo = _repo()
    capture = await repo.get_capture(run_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="capture not found")
    if capture.get("wiped_at"):
        raise HTTPException(status_code=410, detail="capture has been wiped")

    try:
        snaps = await repo.read_snapshots(run_id)
    except ForensicReadError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await _audit(
        actor=f"user:{principal.subject}",
        kind="forensic.read_snapshots",
        target=run_id,
        diff={"snapshot_count": len(snaps)},
    )

    return {
        "capture": capture,
        "snapshots": [
            {
                "id": s.id,
                "iteration": s.iteration,
                "sequence": s.sequence,
                "kind": s.kind,
                "captured_at": s.captured_at,
                "span_id": s.span_id,
                "payload": s.payload,
            }
            for s in snaps
        ],
    }


@router.delete("/{run_id}")
async def wipe_capture(
    run_id: str,
    principal: Principal = Depends(require_admin),
) -> dict:
    """Cryptographically shred a forensic capture.

    Deletes the per-run age identity first, then the snapshot rows,
    then marks the capture row ``wiped_at``. This is a one-way
    operation.
    """
    ok = await _repo().wipe(run_id)
    if not ok:
        raise HTTPException(status_code=404, detail="capture not found")
    await _audit(
        actor=f"user:{principal.subject}",
        kind="forensic.wipe",
        target=run_id,
        severity="elevated",
        reason="admin wipe via API",
    )
    return {"ok": True}


@router.post("/retention/sweep")
async def retention_sweep(
    principal: Principal = Depends(require_admin),
) -> dict:
    """Run the nightly retention sweep on-demand."""
    wiped = await run_retention_sweep()
    await _audit(
        actor=f"user:{principal.subject}",
        kind="forensic.retention_sweep",
        target="forensic_captures",
        diff={"wiped": wiped},
    )
    return {"wiped": wiped}
