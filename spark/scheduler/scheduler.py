"""Spark scheduler — APScheduler with persistent SQLite store.

v1 uses APScheduler 3's `AsyncIOScheduler`; we do not take the dependency on
the pre-release 4.x for public shipping stability. The job store points at a
dedicated SQLite file so we don't collide with the main Spark DB when both
load concurrent writers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from spark.config.models import Agent, Task
from spark.logging import EventType, get_logger
from spark.persistence.db import session_scope
from spark.persistence.models import ScheduleRow
from spark.persistence.repositories import ScheduleRepository
from spark.scheduler.triggers import build_trigger

log = get_logger("spark.scheduler")

DEFAULT_JOBSTORE = Path("~/.spark/scheduler.db").expanduser()


@dataclass
class ScheduledJob:
    task_name: str
    agent_name: str
    trigger_type: str
    trigger_expression: str
    timezone: str


class SparkScheduler:
    """Thin façade over APScheduler with overlap protection and recovery."""

    def __init__(self, *, jobstore_path: Path | None = None) -> None:
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler

        jobstore_path = (jobstore_path or DEFAULT_JOBSTORE).expanduser()
        jobstore_path.parent.mkdir(parents=True, exist_ok=True)
        self._scheduler = AsyncIOScheduler(
            jobstores={"default": SQLAlchemyJobStore(url=f"sqlite:///{jobstore_path}")},
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            },
            timezone="UTC",
        )
        self._handlers: dict[str, Callable[[], Awaitable[Any]]] = {}
        # Event-mode tasks (file watcher, HTTP poller, Telegram bot)
        # run as detached asyncio tasks. Tracked here so we can stop
        # them at shutdown / unschedule.
        self._event_sources: dict[str, Any] = {}

    async def start(self) -> None:
        self._scheduler.start()
        await self.recover()
        self._register_internal_jobs()

    def _register_internal_jobs(self) -> None:
        """Register runtime-wide maintenance jobs from SparkRuntime config.

        Today: memory pruning (H1.2). This is called once at
        ``start()`` and is idempotent — ``replace_existing=True`` means
        a restart won't duplicate jobs.
        """
        try:
            from spark.config.runtime_config import load_runtime

            runtime = load_runtime()
        except Exception as exc:  # pragma: no cover — config is optional
            log.warning("scheduler.runtime_config_load_failed", error=str(exc))
            return

        prune_cfg = runtime.spec.memory_pruning
        if prune_cfg.enabled:
            self._register_memory_pruning_job(prune_cfg)

        self._register_forensic_retention_job()
        self._register_memory_lifecycle_jobs()

    def _register_memory_pruning_job(self, prune_cfg: Any) -> None:
        from apscheduler.triggers.cron import CronTrigger

        try:
            trigger = CronTrigger.from_crontab(prune_cfg.schedule)
        except Exception as exc:
            log.warning(
                "scheduler.memory_pruning_cron_invalid",
                schedule=prune_cfg.schedule,
                error=str(exc),
            )
            return

        async def memory_pruning_job() -> None:
            from spark.memory.pruning_runner import run_memory_pruning_job

            try:
                await run_memory_pruning_job(prune_cfg, actor="scheduler")
            except Exception as exc:  # pragma: no cover — best-effort
                log.warning("memory_pruning_job_failed", error=str(exc))

        self._scheduler.add_job(
            memory_pruning_job,
            trigger=trigger,
            id="spark:memory_pruning",
            replace_existing=True,
            name="spark:memory_pruning",
        )
        log.info(
            "scheduler.memory_pruning_registered",
            schedule=prune_cfg.schedule,
            dry_run=prune_cfg.dry_run,
        )

    def _register_forensic_retention_job(self) -> None:
        """Nightly sweep that cryptographically shreds expired captures."""
        from apscheduler.triggers.cron import CronTrigger

        async def forensic_retention_job() -> None:
            try:
                from spark.forensic.retention import run_retention_sweep

                await run_retention_sweep()
            except Exception as exc:  # pragma: no cover
                log.warning("forensic_retention_job_failed", error=str(exc))

        self._scheduler.add_job(
            forensic_retention_job,
            trigger=CronTrigger.from_crontab("17 3 * * *"),
            id="spark:forensic_retention",
            replace_existing=True,
            name="spark:forensic_retention",
        )

    def _register_memory_lifecycle_jobs(self) -> None:
        """Nightly decay + weekly consolidation + weekly consensus (M1)."""
        from apscheduler.triggers.cron import CronTrigger

        async def decay_job() -> None:
            try:
                from spark.memory.lifecycle import decay_confidence_pass

                await decay_confidence_pass()
            except Exception as exc:  # pragma: no cover
                log.warning("memory_decay_job_failed", error=str(exc))

        async def consolidation_job() -> None:
            try:
                from spark.memory.consolidation import run_consolidation_pass
                from spark.memory.embeddings import SentenceTransformersProvider

                # No chat model available from scheduler context — try
                # to build one from the default agent's provider, skip
                # if unavailable. Consolidation is optional polish.
                chat_model = await _default_chat_model()
                if chat_model is None:
                    return
                await run_consolidation_pass(
                    chat_model=chat_model,
                    embedder=SentenceTransformersProvider(),
                    persist_path=_default_chroma_path(),
                )
            except Exception as exc:  # pragma: no cover
                log.warning(
                    "memory_consolidation_job_failed", error=str(exc)
                )

        async def consensus_job() -> None:
            try:
                from spark.memory.consensus import run_consensus_detection
                from spark.memory.embeddings import SentenceTransformersProvider

                await run_consensus_detection(
                    embedder=SentenceTransformersProvider(),
                    persist_path=_default_chroma_path(),
                )
            except Exception as exc:  # pragma: no cover
                log.warning(
                    "memory_consensus_job_failed", error=str(exc)
                )

        async def synthesis_job() -> None:
            try:
                from spark.memory.synthesis import run_synthesis_for_agent
                from spark.memory.embeddings import SentenceTransformersProvider
                from spark.memory.long_term import LongTermMemory

                chat_model = await _default_chat_model()
                if chat_model is None:
                    return
                agents = await _active_agents_with_ltm()
                for ag_name, ltm_cfg in agents:
                    ltm = LongTermMemory(
                        namespace=ltm_cfg.namespace,
                        collection_name=ltm_cfg.collection,
                        persist_path=_default_chroma_path(),
                        embedder=SentenceTransformersProvider(),
                    )
                    await run_synthesis_for_agent(
                        chat_model=chat_model,
                        long_term=ltm,
                        agent_name=ag_name,
                    )
            except Exception as exc:  # pragma: no cover
                log.warning("memory_synthesis_job_failed", error=str(exc))

        # Nightly at 02:13 — decay.
        self._scheduler.add_job(
            decay_job,
            trigger=CronTrigger.from_crontab("13 2 * * *"),
            id="spark:memory_decay",
            replace_existing=True,
            name="spark:memory_decay",
        )
        # Weekly Sunday 03:30 — consolidation.
        self._scheduler.add_job(
            consolidation_job,
            trigger=CronTrigger.from_crontab("30 3 * * 0"),
            id="spark:memory_consolidation",
            replace_existing=True,
            name="spark:memory_consolidation",
        )
        # Weekly Sunday 04:00 — consensus detection.
        self._scheduler.add_job(
            consensus_job,
            trigger=CronTrigger.from_crontab("0 4 * * 0"),
            id="spark:memory_consensus",
            replace_existing=True,
            name="spark:memory_consensus",
        )
        # Nightly 04:30 — synthesis ("dreams").
        self._scheduler.add_job(
            synthesis_job,
            trigger=CronTrigger.from_crontab("30 4 * * *"),
            id="spark:memory_synthesis",
            replace_existing=True,
            name="spark:memory_synthesis",
        )

    async def shutdown(self) -> None:
        # Cancel every detached event-source task so the runner loops
        # exit cleanly. Without this they'd cling to the asyncio loop
        # past server shutdown and leak connections.
        for name in list(self._event_sources.keys()):
            await self.stop_event_source(name)
        self._scheduler.shutdown(wait=False)

    def register_handler(self, task_name: str, handler: Callable[[], Awaitable[Any]]) -> None:
        self._handlers[task_name] = handler

    async def schedule_task(self, agent: Agent, task: Task) -> None:
        # Event-mode tasks have no APScheduler job — they fire from
        # external sources (file watcher, HTTP poller, Telegram bot, …).
        # We dispatch them through ``start_event_source`` instead.
        if task.spec.on is not None:
            await self.start_event_source(agent, task)
            return

        if task.spec.schedule is None:
            return
        trigger = build_trigger(task.spec.schedule)
        job_id = task.metadata.name

        async def job_runner() -> None:
            log.info(
                "scheduler.tick",
                event_type=EventType.SCHEDULER_TICK,
                task=task.metadata.name,
                agent=agent.metadata.name,
            )
            handler = self._handlers.get(task.metadata.name)
            if handler is not None:
                await handler()
                return
            from spark.scheduler.executor import execute_task_by_name

            await execute_task_by_name(
                task.metadata.name, triggered_by="scheduler"
            )

        self._scheduler.add_job(
            job_runner,
            trigger=trigger,
            id=job_id,
            replace_existing=True,
            name=f"spark:{task.metadata.name}",
        )

        async with session_scope() as session:
            repo = ScheduleRepository(session)
            await repo.upsert(
                ScheduleRow(
                    task_name=task.metadata.name,
                    trigger_type=task.spec.schedule.type,
                    trigger_expression=_trigger_expression(task.spec.schedule),
                    timezone=task.spec.schedule.timezone,
                    enabled=True,
                )
            )

    async def start_event_source(self, agent: Agent, task: Task) -> None:
        """Spin up a detached poller for an event-mode task.

        Dispatches on ``task.spec.on.type`` and binds an ``on_fire``
        callback that pushes through ``execute_task_by_name`` so the
        usual lifecycle / chain / payload-forwarding pipeline applies.

        Idempotent: re-registering a task replaces its source.
        """
        from spark.config.models import (  # noqa: PLC0415
            FileChangedEvent,
            HttpNewRowEvent,
            TelegramBotEvent,
            TelegramMessageEvent,
        )
        from spark.scheduler.events import (  # noqa: PLC0415
            run_file_watcher,
            run_http_poller,
            run_telegram_bot,
            upconvert_legacy,
        )
        from spark.scheduler.executor import execute_task_by_name  # noqa: PLC0415

        if task.spec.on is None:
            return

        # Replace any existing source for this task.
        await self.stop_event_source(task.metadata.name)

        async def on_fire(payload: dict[str, Any]) -> None:
            # Route the inbound event to whatever task the source asked for.
            # File / HTTP sources don't pick a task (they just say "fire me");
            # Telegram bot sources may dispatch to other tasks via /run.
            target = payload.get("task") or task.metadata.name
            await execute_task_by_name(
                target,
                triggered_by=f"event:{task.spec.on.type}",
                payload=payload,
            )

        ev = task.spec.on
        if isinstance(ev, FileChangedEvent):
            coro = run_file_watcher(task.metadata.name, ev, on_fire)
        elif isinstance(ev, HttpNewRowEvent):
            coro = run_http_poller(task.metadata.name, ev, on_fire)
        elif isinstance(ev, TelegramBotEvent):
            coro = run_telegram_bot(task.metadata.name, ev, on_fire)
        elif isinstance(ev, TelegramMessageEvent):
            # Legacy single-task config — upconvert to the bot runner.
            upgraded = upconvert_legacy(ev)
            coro = run_telegram_bot(task.metadata.name, upgraded, on_fire)
        else:  # pragma: no cover — defensive, discriminator exhausts variants
            log.warning(
                "scheduler.unknown_event_type",
                task=task.metadata.name,
                type=type(ev).__name__,
            )
            return

        runner_task = asyncio.create_task(
            coro, name=f"event:{task.metadata.name}"
        )
        self._event_sources[task.metadata.name] = runner_task
        log.info(
            "scheduler.event_source_started",
            task=task.metadata.name,
            agent=agent.metadata.name,
            type=ev.type,
        )

    async def stop_event_source(self, task_name: str) -> None:
        runner = self._event_sources.pop(task_name, None)
        if runner is None:
            return
        runner.cancel()
        try:
            await runner
        except (asyncio.CancelledError, Exception):
            pass
        log.info("scheduler.event_source_stopped", task=task_name)

    async def unschedule(self, task_name: str) -> None:
        try:
            self._scheduler.remove_job(task_name)
        except Exception:  # pragma: no cover — job may not exist
            pass
        await self.stop_event_source(task_name)
        async with session_scope() as session:
            repo = ScheduleRepository(session)
            await repo.delete(task_name)

    async def list_scheduled(self) -> list[ScheduledJob]:
        async with session_scope() as session:
            repo = ScheduleRepository(session)
            rows = await repo.list_all()
        return [
            ScheduledJob(
                task_name=r.task_name,
                agent_name="",
                trigger_type=r.trigger_type,
                trigger_expression=r.trigger_expression,
                timezone=r.timezone,
            )
            for r in rows
        ]

    async def recover(self) -> int:
        """Rebuild transient job list from persistent SQLite job store +
        restart event-mode pollers from the on-disk task YAMLs.

        APScheduler restores cron / interval jobs from its own jobstore
        automatically. Event sources, however, run as detached asyncio
        tasks that don't survive a process restart — we walk the task
        table here and re-start each one.
        """
        from sqlalchemy import select  # noqa: PLC0415

        from spark.persistence.models import TaskRow  # noqa: PLC0415

        jobs = self._scheduler.get_jobs()
        log.info(
            "scheduler.recovered", event_type=EventType.SCHEDULER_TICK, count=len(jobs)
        )

        async with session_scope() as session:
            stmt = select(TaskRow).where(TaskRow.mode == "event")
            event_tasks = list((await session.execute(stmt)).scalars().all())

        recovered = 0
        for row in event_tasks:
            if row.state in ("stopped", "dlq"):
                continue
            try:
                agent, task = await _load_pair(row)
            except Exception as exc:  # pragma: no cover — defensive
                log.warning(
                    "scheduler.event_recovery_load_failed",
                    task=row.name,
                    error=str(exc),
                )
                continue
            if agent is None or task is None:
                continue
            try:
                await self.start_event_source(agent, task)
                recovered += 1
            except Exception as exc:  # pragma: no cover
                log.warning(
                    "scheduler.event_recovery_start_failed",
                    task=row.name,
                    error=str(exc),
                )
        if recovered:
            log.info("scheduler.event_sources_recovered", count=recovered)
        return len(jobs) + recovered


async def _load_pair(row: Any) -> tuple[Any, Any]:
    """Load agent + task YAMLs for an existing TaskRow.

    Returns ``(None, None)`` when files are missing (deleted on disk
    after the row was created). Lets the recovery loop skip cleanly
    rather than crash.
    """
    from pathlib import Path as _P  # noqa: PLC0415

    from spark.config.loader import load_agent, load_task  # noqa: PLC0415

    if not row.config_path:
        return (None, None)
    task_path = _P(row.config_path)
    agent_path = _P("~/.spark/agents").expanduser() / f"{row.agent_name}.yaml"
    if not task_path.exists() or not agent_path.exists():
        return (None, None)
    return (load_agent(agent_path), load_task(task_path))


def _trigger_expression(schedule: Any) -> str:
    return getattr(schedule, "expression", None) or f"every {getattr(schedule, 'seconds', 0)}s"


def _default_chroma_path() -> str:
    from pathlib import Path as _P  # noqa: PLC0415

    from spark.config.runtime_config import get_data_volume  # noqa: PLC0415

    dv = get_data_volume()
    if dv is not None:
        return str(dv.chroma_path)
    return str(_P("~/.spark/chroma").expanduser())


async def _default_chat_model() -> Any | None:
    """Build a chat model from the first agent that has a provider configured.

    Memory consolidation and synthesis need a model; when no agents
    are installed yet, we return None and skip gracefully.
    """
    from pathlib import Path as _P  # noqa: PLC0415

    from spark.config.loader import load_agent  # noqa: PLC0415
    from spark.providers.factory import build_chat_model  # noqa: PLC0415
    from spark.runtime import get_secret_manager  # noqa: PLC0415

    agents_dir = _P("~/.spark/agents").expanduser()
    if not agents_dir.is_dir():
        return None
    for yaml_path in sorted(agents_dir.glob("*.yaml")):
        try:
            agent = load_agent(yaml_path)
            return build_chat_model(
                agent.spec.runtime.provider, get_secret_manager()
            )
        except Exception:
            continue
    return None


async def _active_agents_with_ltm() -> list[tuple[str, Any]]:
    """Return (agent_name, ltm_cfg) tuples for agents with LTM enabled."""
    from pathlib import Path as _P  # noqa: PLC0415

    from spark.config.loader import load_agent  # noqa: PLC0415

    agents_dir = _P("~/.spark/agents").expanduser()
    out: list[tuple[str, Any]] = []
    if not agents_dir.is_dir():
        return out
    for yaml_path in sorted(agents_dir.glob("*.yaml")):
        try:
            agent = load_agent(yaml_path)
            ltm = agent.spec.memory.long_term_memory
            if ltm is not None and ltm.enabled:
                out.append((agent.metadata.name, ltm))
        except Exception:
            continue
    return out
