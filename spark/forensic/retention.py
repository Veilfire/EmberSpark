"""Forensic retention — nightly TTL sweep.

Deletes forensic captures whose ``expires_at`` has passed. This
calls :meth:`ForensicRepository.wipe` for each expired run so the
per-run identity is cryptographically shredded before the rows are
dropped — same code path as a manual wipe.
"""

from __future__ import annotations

from sqlalchemy import select

from spark.forensic.reader import ForensicRepository
from spark.logging import get_logger
from spark.persistence.db import session_scope
from spark.persistence.learning_models import ForensicCaptureRow
from spark.utils.time import utcnow

log = get_logger("spark.forensic")


async def run_retention_sweep() -> int:
    """Delete expired forensic captures. Returns the number wiped."""
    from spark.runtime.bootstrap import get_secret_manager  # noqa: PLC0415

    now = utcnow()
    wiped = 0

    async with session_scope() as session:
        stmt = select(ForensicCaptureRow).where(
            ForensicCaptureRow.wiped_at.is_(None),
            ForensicCaptureRow.expires_at <= now,
        )
        rows = (await session.execute(stmt)).scalars().all()
        expired_ids = [r.run_id for r in rows]

    if not expired_ids:
        return 0

    repo = ForensicRepository(get_secret_manager())
    for run_id in expired_ids:
        try:
            ok = await repo.wipe(run_id)
            if ok:
                wiped += 1
        except Exception as exc:  # pragma: no cover — best effort
            log.warning("forensic.retention_sweep_failed", run_id=run_id, error=str(exc))

    log.info("forensic.retention_swept", wiped=wiped)
    return wiped
