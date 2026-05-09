"""Retention-class-driven pruning for long-term memory.

Two entry points:

- ``prune_expired(long_term)`` — legacy single-namespace sweep used by
  tests and the old scheduler tick. Uses the hard-coded default
  retention windows. Kept for backwards compatibility.
- ``run_pruning_pass(cfg)`` — the H1.2 sweep. Walks every namespace in
  the index, applies the per-retention-class rollover windows from
  ``MemoryPruningConfig``, and returns a structured ``PruningReport``
  so callers can emit notifications, audit entries, and UI state from
  the same data.

Both delete from SQLite *and* the Chroma collection — missing either
side would leave the index out of sync.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import select

from spark.memory.embeddings import SentenceTransformersProvider
from spark.memory.long_term import LongTermMemory
from spark.persistence.db import session_scope
from spark.persistence.models import LongTermMemoryIndexRow

if TYPE_CHECKING:
    from spark.config.runtime_config import MemoryPruningConfig


_LEGACY_RETENTION_TTL = {
    "temporary": timedelta(days=7),
    "expiring": timedelta(days=30),
    "review": timedelta(days=180),
    "persistent": None,
}


@dataclass
class PruningReport:
    """Result of a pruning sweep.

    ``by_class`` is a per-retention-class count of rows deleted.
    ``dry_run`` preserves whether the counts are hypothetical.
    ``namespaces`` lists every namespace the sweep touched — useful for
    future per-namespace UIs without changing the shape of this report.
    """

    total: int = 0
    by_class: dict[str, int] = field(default_factory=dict)
    namespaces: list[str] = field(default_factory=list)
    dry_run: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "total": self.total,
            "by_class": dict(self.by_class),
            "namespaces": list(self.namespaces),
            "dry_run": self.dry_run,
        }


async def prune_expired(long_term: LongTermMemory) -> int:
    """Delete long-term memory rows whose retention class has expired.

    Legacy single-namespace sweep. Use :func:`run_pruning_pass` for the
    configurable H1.2 path.
    """
    now = datetime.now(tz=timezone.utc)
    removed = 0
    async with session_scope() as session:
        stmt = select(LongTermMemoryIndexRow).where(
            LongTermMemoryIndexRow.namespace == long_term.namespace
        )
        result = await session.execute(stmt)
        for row in result.scalars().all():
            ttl = _LEGACY_RETENTION_TTL.get(row.retention_class)
            if ttl is None:
                continue
            created = row.created_at
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if now - created > ttl:
                long_term.delete(row.memory_id)
                await session.delete(row)
                removed += 1
    return removed


def _resolve_chroma_path() -> Path:
    """Return the on-disk Chroma root, honoring the active data volume."""
    from spark.config.runtime_config import get_data_volume

    dv = get_data_volume()
    if dv is not None:
        return dv.chroma_path
    return Path("~/.spark/chroma").expanduser()


async def run_pruning_pass(cfg: "MemoryPruningConfig") -> PruningReport:
    """Run a pruning sweep across every namespace in the index.

    Reads ``cfg.rollover_windows`` to decide per-class TTLs (``None`` =
    never prune). When ``cfg.dry_run`` is True, counts are computed but
    no rows are deleted from SQLite or Chroma.
    """
    report = PruningReport(dry_run=cfg.dry_run)

    windows = cfg.rollover_windows
    ttls: dict[str, timedelta | None] = {
        "temporary": timedelta(days=windows.temporary) if windows.temporary else None,
        "expiring": timedelta(days=windows.expiring) if windows.expiring else None,
        "review": timedelta(days=windows.review) if windows.review else None,
        "persistent": timedelta(days=windows.persistent) if windows.persistent else None,
    }

    # Short-circuit: if every class is None ("keep forever"), there's
    # nothing to do. Cheap guard before scanning the index.
    if not any(ttls.values()):
        return report

    now = datetime.now(tz=timezone.utc)
    persist_path = _resolve_chroma_path()

    # We batch rows by (namespace, collection) to amortize Chroma
    # connections across an entire namespace's worth of deletes.
    namespace_buckets: dict[tuple[str, str], list[LongTermMemoryIndexRow]] = {}

    async with session_scope() as session:
        stmt = select(LongTermMemoryIndexRow)
        result = await session.execute(stmt)
        rows = list(result.scalars().all())

        for row in rows:
            ttl = ttls.get(row.retention_class)
            if ttl is None:
                continue
            updated = row.updated_at or row.created_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            if now - updated <= ttl:
                continue
            namespace_buckets.setdefault(
                (row.namespace, row.collection), []
            ).append(row)

        # Dry-run only tallies; no deletes, no Chroma touches.
        if cfg.dry_run:
            for bucket_rows in namespace_buckets.values():
                for row in bucket_rows:
                    report.total += 1
                    report.by_class[row.retention_class] = (
                        report.by_class.get(row.retention_class, 0) + 1
                    )
            report.namespaces = sorted({ns for ns, _ in namespace_buckets.keys()})
            return report

        embedder = SentenceTransformersProvider() if namespace_buckets else None

        for (namespace, collection), bucket_rows in namespace_buckets.items():
            ltm = LongTermMemory(
                namespace=namespace,
                collection_name=collection,
                persist_path=persist_path,
                embedder=embedder,  # type: ignore[arg-type]
            )
            for row in bucket_rows:
                try:
                    ltm.delete(row.memory_id)
                except Exception:  # pragma: no cover — best effort
                    # Chroma delete is idempotent; a missing id is fine.
                    pass
                await session.delete(row)
                report.total += 1
                report.by_class[row.retention_class] = (
                    report.by_class.get(row.retention_class, 0) + 1
                )

    report.namespaces = sorted({ns for ns, _ in namespace_buckets.keys()})
    return report
