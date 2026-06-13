"""Forensic reader — admin-gated decrypt + structured view.

Reads the capture row + snapshot rows for one run, decrypts each
``payload_encrypted`` blob against the per-run age identity (stored
in the secrets vault under ``forensic:<run_id>``), and returns a
structured dict the web UI + CLI can render.

All read operations here are expected to be gated by
``require_admin`` in the API layer. Every read is audited at ``info``
severity so an operator can review who decrypted what and when.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from spark.logging import get_logger
from spark.persistence.db import session_scope
from spark.persistence.learning_models import (
    ForensicCaptureRow,
    ForensicSnapshotRow,
)
from spark.utils.time import isoformat

if TYPE_CHECKING:  # pragma: no cover
    from spark.secrets import SecretManager

log = get_logger("spark.forensic")


@dataclass
class DecryptedSnapshot:
    id: int
    iteration: int
    sequence: int
    kind: str
    captured_at: str
    span_id: int | None
    payload: dict[str, Any]


class ForensicRepository:
    """Thin read-only query surface over the forensic tables."""

    def __init__(self, secrets: "SecretManager") -> None:
        self.secrets = secrets

    async def list_captures(self) -> list[dict[str, Any]]:
        async with session_scope() as session:
            stmt = select(ForensicCaptureRow).order_by(
                ForensicCaptureRow.captured_at.desc()
            )
            rows = (await session.execute(stmt)).scalars().all()
            return [self._capture_to_dict(r) for r in rows]

    async def get_capture(self, run_id: str) -> dict[str, Any] | None:
        async with session_scope() as session:
            row = await session.get(ForensicCaptureRow, run_id)
            if row is None:
                return None
            return self._capture_to_dict(row)

    async def read_snapshots(self, run_id: str) -> list[DecryptedSnapshot]:
        """Return every snapshot for a run, decrypted and parsed.

        Raises :class:`ForensicReadError` when the per-run identity is
        missing from the vault (e.g. after ``wipe()``) or decryption
        fails for any other reason.
        """
        identity = self._load_identity(run_id)

        try:
            from pyrage import decrypt
        except ImportError as exc:  # pragma: no cover
            raise ForensicReadError("pyrage not installed") from exc

        async with session_scope() as session:
            stmt = (
                select(ForensicSnapshotRow)
                .where(ForensicSnapshotRow.run_id == run_id)
                .order_by(
                    ForensicSnapshotRow.iteration.asc(),
                    ForensicSnapshotRow.sequence.asc(),
                )
            )
            rows = (await session.execute(stmt)).scalars().all()

        out: list[DecryptedSnapshot] = []
        for row in rows:
            try:
                plaintext = decrypt(row.payload_encrypted, [identity])
            except Exception as exc:
                raise ForensicReadError(
                    f"decrypt failed for snapshot id={row.id}: {exc}"
                ) from exc
            try:
                payload = json.loads(plaintext.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ForensicReadError(
                    f"snapshot id={row.id} not JSON: {exc}"
                ) from exc
            out.append(
                DecryptedSnapshot(
                    id=row.id or 0,
                    iteration=row.iteration,
                    sequence=row.sequence,
                    kind=row.kind,
                    captured_at=isoformat(row.captured_at),
                    span_id=row.span_id,
                    payload=payload,
                )
            )
        return out

    async def wipe(self, run_id: str) -> bool:
        """Cryptographically shred a run's capture.

        1. Delete the per-run identity from the vault (shred).
        2. Delete the snapshot rows.
        3. Mark the capture row ``wiped_at`` (kept for audit trail).

        Returns True if a capture was found and wiped.
        """
        async with session_scope() as session:
            capture = await session.get(ForensicCaptureRow, run_id)
            if capture is None:
                return False

            # 1. Cryptographic shred — drop the vault key first so that
            # even if the snapshot deletes fail below, the data is
            # already unreadable.
            try:
                self.secrets.delete(capture.vault_key)
            except Exception as exc:  # pragma: no cover — secrets may be gone
                log.warning("forensic.vault_delete_failed", run_id=run_id, error=str(exc))

            # 2. Drop the snapshot rows.
            stmt = select(ForensicSnapshotRow).where(
                ForensicSnapshotRow.run_id == run_id
            )
            snaps = (await session.execute(stmt)).scalars().all()
            for snap in snaps:
                await session.delete(snap)

            # 3. Mark the capture row wiped but retain it for audit.
            from spark.utils.time import utcnow  # noqa: PLC0415

            capture.wiped_at = utcnow()
            session.add(capture)

        log.info("forensic.wiped", run_id=run_id)
        return True

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _load_identity(self, run_id: str) -> Any:
        try:
            from pyrage import x25519
        except ImportError as exc:  # pragma: no cover
            raise ForensicReadError("pyrage not installed") from exc

        vault_key = f"forensic:{run_id}"
        try:
            identity_str = self.secrets.get(vault_key).get_secret_value()
        except Exception as exc:
            raise ForensicReadError(
                f"per-run identity missing for {run_id!r} — run may be wiped"
            ) from exc
        return x25519.Identity.from_str(identity_str)

    @staticmethod
    def _capture_to_dict(row: ForensicCaptureRow) -> dict[str, Any]:
        return {
            "run_id": row.run_id,
            "agent_name": row.agent_name,
            "task_name": row.task_name,
            "enabled_by": row.enabled_by,
            "enabled_reason": row.enabled_reason,
            "captured_at": isoformat(row.captured_at) if row.captured_at else None,
            "expires_at": isoformat(row.expires_at) if row.expires_at else None,
            "iteration_count": row.iteration_count,
            "snapshot_count": row.snapshot_count,
            "wiped_at": isoformat(row.wiped_at) if row.wiped_at else None,
        }


class ForensicReadError(RuntimeError):
    """Raised when a forensic capture can't be decrypted."""
