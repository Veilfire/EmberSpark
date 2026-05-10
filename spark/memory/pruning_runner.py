"""End-to-end pruning orchestration.

Wraps :func:`spark.memory.pruning.run_pruning_pass` with the
cross-cutting concerns the API routes, the CLI, and the scheduler all
need: audit log entries, ``memory.pruned`` structured logging, and
``MEMORY_PRUNED`` notifications (when the config opts in and any rows
were actually deleted).

Keeping this separate from ``pruning.py`` lets unit tests exercise the
pure sweep logic without standing up the notification service or an
audit repository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from spark.logging import EventType, get_logger
from spark.memory.pruning import PruningReport, run_pruning_pass

# spark.notifications imports spark.web.events which can trigger a
# circular load when this module is reached from a cold-start CLI.
# Defer the import inside the only function that uses it.
from spark.persistence.db import session_scope
from spark.persistence.learning_repos import AuditRepository

if TYPE_CHECKING:
    from spark.config.runtime_config import MemoryPruningConfig

log = get_logger("spark.memory.pruning")


async def run_memory_pruning_job(
    cfg: "MemoryPruningConfig",
    *,
    actor: str = "scheduler",
    force_dry_run: bool | None = None,
) -> PruningReport:
    """Run a sweep, emit audit + log + notification, return the report.

    ``force_dry_run`` lets the UI "Run dry-run now" button override the
    config without mutating the stored config. ``None`` falls through to
    ``cfg.dry_run``.
    """
    effective_dry_run = cfg.dry_run if force_dry_run is None else force_dry_run

    # Shallow copy via model_copy so we don't mutate the caller's cfg.
    if force_dry_run is not None and force_dry_run != cfg.dry_run:
        cfg = cfg.model_copy(update={"dry_run": force_dry_run})

    report = await run_pruning_pass(cfg)

    log.info(
        "memory.pruned",
        event_type=EventType.MEMORY_PRUNED,
        total=report.total,
        by_class=report.by_class,
        namespaces=report.namespaces,
        dry_run=effective_dry_run,
        actor=actor,
    )

    async with session_scope() as session:
        await AuditRepository(session).append(
            actor=actor,
            kind="memory.pruned",
            target="long_term_memory",
            reason=(
                f"dry-run: {report.total} rows would be pruned"
                if effective_dry_run
                else f"pruned {report.total} rows"
            ),
            diff={
                "total": report.total,
                "by_class": dict(report.by_class),
                "namespaces": list(report.namespaces),
                "dry_run": effective_dry_run,
            },
            severity="info",
        )

    if cfg.notify_on_prune and not effective_dry_run and report.total > 0:
        from spark.notifications import (  # noqa: PLC0415
            NotificationKind,
            get_notification_service,
        )

        svc = get_notification_service()
        summary = ", ".join(
            f"{cls}:{count}" for cls, count in sorted(report.by_class.items())
        )
        await svc.notify(
            kind=NotificationKind.MEMORY_PRUNED,
            title=f"Pruned {report.total} memories",
            body=summary or None,
            severity="info",
            target_kind="memory",
        )

    return report
