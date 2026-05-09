"""Spark CLI — Typer entry point."""

from __future__ import annotations

import asyncio
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from spark.config.loader import ConfigLoadError, load_agent, load_task
from spark.config.validator import validate_agent, validate_task
from spark.logging import configure_logging, get_logger
from spark.persistence.db import init_db
from spark.plugins.registry import default_registry
from spark.runtime.lifecycle import Lifecycle
from spark.secrets import SecretNotFound

app = typer.Typer(
    name="spark",
    help="Spark — an open source agent runtime for bounded autonomy.",
    no_args_is_help=True,
)

agent_app = typer.Typer(help="Agent commands")
task_app = typer.Typer(help="Task commands")
schedule_app = typer.Typer(help="Schedule commands")
logs_app = typer.Typer(help="Log tail")
memory_app = typer.Typer(help="Memory inspection")
doctor_app = typer.Typer(help="Host diagnostics")
skills_app = typer.Typer(help="Skill review")
config_app = typer.Typer(help="Runtime config")
daemon_app = typer.Typer(help="Daemonization")
secrets_app = typer.Typer(help="Age-encrypted secret vault")
template_app = typer.Typer(help="Ready-to-run agent templates")
forensic_app = typer.Typer(help="Forensic capture inspection (H2)")

app.add_typer(agent_app, name="agent")
app.add_typer(task_app, name="task")
app.add_typer(schedule_app, name="schedule")
app.add_typer(logs_app, name="logs")
app.add_typer(memory_app, name="memory")
app.add_typer(doctor_app, name="doctor")
app.add_typer(skills_app, name="skills")
app.add_typer(config_app, name="config")
app.add_typer(daemon_app, name="daemon")
app.add_typer(secrets_app, name="secrets")
app.add_typer(template_app, name="template")
app.add_typer(forensic_app, name="forensic")

console = Console()
err_console = Console(stderr=True, style="red")


def _setup() -> None:
    # Resolve the SparkRuntime YAML side-effects (data volume mkdir +
    # process-scoped data volume singleton, age vault auto-init,
    # process-scoped SecretManager). This makes `get_data_volume()`,
    # `get_secret_manager()`, and the effective SQLite path available
    # to subsequent `init_db()` calls. Failures here are non-fatal —
    # the YAML might be missing, which is fine for commands that don't
    # touch the data volume or the vault.
    try:
        from spark.runtime.bootstrap import bootstrap

        bootstrap()
    except Exception as exc:  # pragma: no cover — best-effort
        import warnings

        warnings.warn(f"bootstrap failed (non-fatal): {exc}", stacklevel=1)

    from spark.runtime import get_secret_manager

    secrets = get_secret_manager()
    configure_logging(tracked_secret_values=secrets.known_values)


# ---------------------------------------------------------------------------
# agent commands
# ---------------------------------------------------------------------------


@agent_app.command("validate")
def agent_validate(path: Path) -> None:
    """Validate an agent YAML file."""
    _setup()
    try:
        agent = load_agent(path)
    except ConfigLoadError as exc:
        err_console.print(f"[bold]{exc.path}[/bold]")
        for e in exc.errors:
            err_console.print(f"  - {e}")
        raise typer.Exit(code=1) from exc

    issues = validate_agent(agent)
    if issues:
        console.print(f"[yellow]Agent '{agent.metadata.name}' loaded with warnings:[/yellow]")
        for i in issues:
            console.print(f"  [yellow]!{i.code}[/yellow]: {i.message}")
    else:
        console.print(f"[green]Agent '{agent.metadata.name}' is valid[/green]")


# ---------------------------------------------------------------------------
# task commands
# ---------------------------------------------------------------------------


@task_app.command("run")
def task_run(
    task_path: Path,
    agent_path: Path = typer.Option(..., "--agent", "-a", help="Agent YAML to use"),
    forensic: str | None = typer.Option(
        None,
        "--forensic",
        help="Enable forensic capture for this run (reason required)",
    ),
) -> None:
    """Run a one-shot task end to end."""
    _setup()
    log = get_logger("spark.cli")
    try:
        agent = load_agent(agent_path)
        task = load_task(task_path)
    except ConfigLoadError as exc:
        err_console.print(f"{exc.path}: {exc.errors}")
        raise typer.Exit(code=1) from exc

    if forensic is not None:
        if not forensic.strip():
            err_console.print("[red]--forensic requires a non-empty reason[/red]")
            raise typer.Exit(code=2)
        task = task.model_copy(
            update={
                "spec": task.spec.model_copy(
                    update={
                        "forensic": task.spec.forensic.model_copy(
                            update={"enabled": True, "reason": forensic}
                        )
                    }
                )
            }
        )

    for issue in validate_agent(agent) + validate_task(task):
        console.print(f"[yellow]!{issue.code}[/yellow] {issue.message}")

    async def _run() -> None:
        from spark.runtime import get_secret_manager

        await init_db()
        secrets = get_secret_manager()
        registry = default_registry()
        lifecycle = Lifecycle(secrets=secrets, registry=registry)
        await lifecycle.register(agent, task)
        result = await lifecycle.run_once(agent, task)
        console.print(f"[bold]run_id[/bold] {result.run_id}")
        console.print(f"[bold]state[/bold]  {result.state.value}")
        console.print(f"[bold]summary[/bold] {result.summary}")
        if result.error:
            console.print(f"[red]{result.error}[/red]")
        log.info("cli.run_complete", run_id=result.run_id, state=result.state.value)

    asyncio.run(_run())


@task_app.command("validate")
def task_validate(path: Path) -> None:
    """Validate a task YAML file."""
    _setup()
    try:
        task = load_task(path)
    except ConfigLoadError as exc:
        err_console.print(f"{exc.path}: {exc.errors}")
        raise typer.Exit(code=1) from exc
    issues = validate_task(task)
    if issues:
        for i in issues:
            console.print(f"[yellow]!{i.code}[/yellow] {i.message}")
    else:
        console.print(f"[green]Task '{task.metadata.name}' is valid[/green]")


@task_app.command("list")
def task_list() -> None:
    """List persisted tasks."""
    _setup()

    async def _run() -> None:
        from spark.persistence.db import session_scope
        from spark.persistence.repositories import TaskRepository

        await init_db()
        async with session_scope() as session:
            repo = TaskRepository(session)
            rows = await repo.list_all()
        table = Table("name", "agent", "mode", "state")
        for r in rows:
            table.add_row(r.name, r.agent_name, r.mode, r.state)
        console.print(table)

    asyncio.run(_run())


@task_app.command("create")
def task_create(
    task_path: Path,
    agent_path: Path = typer.Option(..., "--agent", "-a", help="Agent YAML to use"),
) -> None:
    """Register a task in the DB without running it.

    Loads + validates both YAMLs, then upserts the agent and task rows.
    Mode (one_shot / recurring / perpetual / event) is taken from the
    task spec; for recurring / perpetual, the schedule is wired in but
    not yet started — call ``spark task start <name>`` to begin.
    """
    _setup()
    log = get_logger("spark.cli")
    try:
        agent = load_agent(agent_path)
        task = load_task(task_path)
    except ConfigLoadError as exc:
        err_console.print(f"{exc.path}: {exc.errors}")
        raise typer.Exit(code=1) from exc

    issues = validate_agent(agent) + validate_task(task)
    for issue in issues:
        console.print(f"[yellow]!{issue.code}[/yellow] {issue.message}")

    async def _run() -> None:
        from spark.runtime import get_secret_manager

        await init_db()
        secrets = get_secret_manager()
        registry = default_registry()
        lifecycle = Lifecycle(secrets=secrets, registry=registry)
        await lifecycle.register(agent, task, config_path=str(task_path))
        console.print(
            f"[green]Created[/green] task '{task.metadata.name}' "
            f"(agent '{agent.metadata.name}', mode {task.spec.mode.value})"
        )
        log.info(
            "cli.task_created",
            task=task.metadata.name,
            agent=agent.metadata.name,
            mode=task.spec.mode.value,
        )

    asyncio.run(_run())


@task_app.command("start")
def task_start(
    task_name: str,
    agent_path: Path = typer.Option(
        ...,
        "--agent",
        "-a",
        help="Agent YAML — required for runtime context (provider, plugins, etc.)",
    ),
) -> None:
    """Start a previously-created task.

    For one-shot tasks: executes once and prints the result. For
    recurring or perpetual tasks: schedules them with the runtime
    scheduler so they fire on their cron / interval. Errors if the task
    is not in the DB (run ``spark task create`` first).
    """
    _setup()
    log = get_logger("spark.cli")
    try:
        agent = load_agent(agent_path)
    except ConfigLoadError as exc:
        err_console.print(f"{exc.path}: {exc.errors}")
        raise typer.Exit(code=1) from exc

    async def _run() -> None:
        from spark.config.enums import TaskMode
        from spark.config.loader import load_task as _load_task
        from spark.persistence.db import session_scope
        from spark.persistence.repositories import TaskRepository
        from spark.runtime import get_secret_manager
        from spark.scheduler.scheduler import SparkScheduler

        await init_db()
        async with session_scope() as session:
            row = await TaskRepository(session).get(task_name)
        if row is None:
            err_console.print(
                f"[red]Task '{task_name}' not registered. "
                "Run `spark task create` first.[/red]"
            )
            raise typer.Exit(code=1)
        if not row.config_path:
            err_console.print(
                f"[red]Task '{task_name}' has no on-disk config_path; "
                "cannot reload.[/red]"
            )
            raise typer.Exit(code=1)

        task = _load_task(Path(row.config_path))
        secrets = get_secret_manager()
        registry = default_registry()
        lifecycle = Lifecycle(secrets=secrets, registry=registry)

        if task.spec.mode is TaskMode.ONE_SHOT:
            result = await lifecycle.run_once(agent, task)
            console.print(f"[bold]run_id[/bold] {result.run_id}")
            console.print(f"[bold]state[/bold]  {result.state.value}")
            console.print(f"[bold]summary[/bold] {result.summary or '(none)'}")
            log.info(
                "cli.task_started",
                task=task_name,
                mode="one_shot",
                run_id=result.run_id,
            )
        else:
            scheduler = SparkScheduler()
            await scheduler.start()
            await scheduler.schedule_task(agent, task)
            console.print(
                f"[green]Scheduled[/green] '{task_name}' "
                f"({task.spec.mode.value})"
            )
            log.info("cli.task_started", task=task_name, mode=task.spec.mode.value)

    asyncio.run(_run())


@task_app.command("stop")
def task_stop(task_name: str) -> None:
    """Stop a running or scheduled task.

    Marks the task row state as ``stopped`` and removes any APScheduler
    job. In-flight runs are not forcibly cancelled — they finish their
    current iteration and exit on the next budget check.
    """
    _setup()
    log = get_logger("spark.cli")

    async def _run() -> None:
        from spark.persistence.db import session_scope
        from spark.persistence.repositories import TaskRepository
        from spark.scheduler.scheduler import SparkScheduler

        await init_db()
        async with session_scope() as session:
            repo = TaskRepository(session)
            row = await repo.get(task_name)
            if row is None:
                err_console.print(f"[red]Task '{task_name}' not found.[/red]")
                raise typer.Exit(code=1)
            await repo.set_state(task_name, "stopped")

        try:
            scheduler = SparkScheduler()
            await scheduler.unschedule(task_name)
        except Exception as exc:  # pragma: no cover — scheduler may not be running
            log.warning("cli.unschedule_failed", task=task_name, error=str(exc))

        console.print(f"[green]Stopped[/green] '{task_name}'")
        log.info("cli.task_stopped", task=task_name)

    asyncio.run(_run())


@task_app.command("inspect")
def task_inspect(task_name: str) -> None:
    """Show task details, recent runs, and active schedule."""
    _setup()

    async def _run() -> None:
        from sqlalchemy import select

        from spark.persistence.db import session_scope
        from spark.persistence.models import TaskRunRow
        from spark.persistence.repositories import (
            ScheduleRepository,
            TaskRepository,
        )
        from spark.utils.time import isoformat

        await init_db()
        async with session_scope() as session:
            row = await TaskRepository(session).get(task_name)
            if row is None:
                err_console.print(f"[red]Task '{task_name}' not found.[/red]")
                raise typer.Exit(code=1)
            stmt = (
                select(TaskRunRow)
                .where(TaskRunRow.task_name == task_name)
                .order_by(TaskRunRow.started_at.desc())
                .limit(5)
            )
            result = await session.execute(stmt)
            recent_runs = list(result.scalars().all())
            schedules = await ScheduleRepository(session).list_all()

        # Task summary
        console.print(f"[bold]Task:[/bold] {row.name}")
        console.print(f"  agent       {row.agent_name}")
        console.print(f"  mode        {row.mode}")
        console.print(f"  state       {row.state}")
        console.print(f"  config      {row.config_path or '(in-memory)'}")
        console.print(f"  updated_at  {isoformat(row.updated_at)}")

        # Active schedule (if any)
        sched = next((s for s in schedules if s.task_name == task_name), None)
        if sched is not None:
            console.print(
                f"[bold]Schedule:[/bold] {sched.trigger_type}={sched.trigger_expression} "
                f"tz={sched.timezone} enabled={sched.enabled}"
            )
        else:
            console.print("[dim]No active schedule.[/dim]")

        # Recent runs
        if recent_runs:
            console.print("[bold]Recent runs:[/bold]")
            table = Table("run_id", "state", "started", "iter", "tool", "model")
            for r in recent_runs:
                table.add_row(
                    r.run_id,
                    r.state,
                    isoformat(r.started_at) if r.started_at else "—",
                    str(r.iterations),
                    str(r.tool_calls),
                    str(r.model_calls),
                )
            console.print(table)
        else:
            console.print("[dim]No runs recorded yet.[/dim]")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# schedule commands
# ---------------------------------------------------------------------------


@schedule_app.command("list")
def schedule_list() -> None:
    _setup()

    async def _run() -> None:
        from spark.persistence.db import session_scope
        from spark.persistence.repositories import ScheduleRepository

        await init_db()
        async with session_scope() as session:
            repo = ScheduleRepository(session)
            rows = await repo.list_all()
        table = Table("task", "trigger", "expression", "tz", "enabled")
        for r in rows:
            table.add_row(
                r.task_name,
                r.trigger_type,
                r.trigger_expression,
                r.timezone,
                "yes" if r.enabled else "no",
            )
        console.print(table)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# logs commands
# ---------------------------------------------------------------------------


@logs_app.command("verify")
def logs_verify(
    root: Path = typer.Option(Path("~/.spark/logs"), "--root"),
) -> None:
    """Walk rotated log files and verify the hash chain."""
    from spark.logging.retention import verify_chain

    verdict = verify_chain(root)
    if verdict.ok:
        console.print(f"[green]chain OK[/green] — head hash {verdict.actual_hash}")
        return
    err_console.print(
        f"[red]chain broken[/red]\n  file:     {verdict.broken_file}\n"
        f"  expected: {verdict.expected_hash}\n"
        f"  actual:   {verdict.actual_hash}\n"
        f"  reason:   {verdict.message}"
    )
    raise typer.Exit(code=1)


@logs_app.command("tail")
def logs_tail(
    path: Path = typer.Option(Path("~/.spark/logs/spark.jsonl"), "--path"),
    follow: bool = typer.Option(True, "--follow/--no-follow"),
) -> None:
    """Tail the JSONL log."""
    import json as _json
    import time

    p = path.expanduser()
    if not p.exists():
        err_console.print(f"log not found: {p}")
        raise typer.Exit(1)
    with p.open("r", encoding="utf-8") as f:
        f.seek(0, 2)
        while True:
            line = f.readline()
            if not line:
                if not follow:
                    break
                time.sleep(0.2)
                continue
            try:
                payload = _json.loads(line)
            except _json.JSONDecodeError:
                console.print(line.rstrip())
                continue
            event = payload.get("event_type", payload.get("event", ""))
            console.print(f"[dim]{payload.get('timestamp','')}[/dim] [bold]{event}[/bold] {payload}")


# ---------------------------------------------------------------------------
# memory commands
# ---------------------------------------------------------------------------


@memory_app.command("query")
def memory_query(
    namespace: str,
    query: str,
    top_k: int = typer.Option(6, "--top-k"),
) -> None:
    _setup()

    async def _run() -> None:
        from spark.config.enums import PrivacyMode
        from spark.memory.embeddings import SentenceTransformersProvider
        from spark.memory.long_term import LongTermMemory
        from spark.memory.retrieval import retrieve

        ltm = LongTermMemory(
            namespace=namespace,
            collection_name=namespace,
            persist_path=Path("~/.spark/chroma"),
            embedder=SentenceTransformersProvider(),
        )
        hits = await retrieve(
            long_term=ltm,
            query=query,
            privacy_mode=PrivacyMode.STRICT,
            top_k=top_k,
        )
        table = Table("id", "type", "score", "summary")
        for h in hits:
            table.add_row(h.memory_id, h.memory_type, f"{h.score:.3f}", h.summary[:80])
        console.print(table)

    asyncio.run(_run())


@memory_app.command("prune")
def memory_prune(
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Compute counts without deleting anything"
    ),
) -> None:
    """Run the retention pruning sweep against long-term memory now."""
    _setup()

    async def _run() -> None:
        from spark.config.runtime_config import load_runtime
        from spark.memory.pruning_runner import run_memory_pruning_job

        cfg = load_runtime().spec.memory_pruning
        report = await run_memory_pruning_job(
            cfg,
            actor="cli",
            force_dry_run=dry_run if dry_run else None,
        )
        prefix = "[yellow]dry-run[/yellow] " if report.dry_run else ""
        console.print(f"{prefix}pruned {report.total} rows")
        if report.by_class:
            table = Table("retention class", "count")
            for cls, count in sorted(report.by_class.items()):
                table.add_row(cls, str(count))
            console.print(table)

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# forensic commands (H2)
# ---------------------------------------------------------------------------


@forensic_app.command("list")
def forensic_list() -> None:
    """List every active forensic capture."""
    _setup()

    async def _run() -> None:
        from spark.forensic.reader import ForensicRepository
        from spark.runtime import get_secret_manager

        await init_db()
        repo = ForensicRepository(get_secret_manager())
        rows = await repo.list_captures()
        if not rows:
            console.print("[dim]no forensic captures[/dim]")
            return
        table = Table("run_id", "agent", "task", "captured_at", "expires_at", "snapshots", "wiped")
        for r in rows:
            table.add_row(
                r["run_id"],
                r["agent_name"],
                r["task_name"],
                str(r["captured_at"]),
                str(r["expires_at"]),
                str(r["snapshot_count"]),
                "yes" if r["wiped_at"] else "no",
            )
        console.print(table)

    asyncio.run(_run())


@forensic_app.command("show")
def forensic_show(run_id: str) -> None:
    """Show metadata for a specific forensic capture."""
    _setup()

    async def _run() -> None:
        from spark.forensic.reader import ForensicRepository
        from spark.runtime import get_secret_manager

        await init_db()
        repo = ForensicRepository(get_secret_manager())
        capture = await repo.get_capture(run_id)
        if capture is None:
            err_console.print(f"capture {run_id!r} not found")
            raise typer.Exit(code=1)
        for k, v in capture.items():
            console.print(f"[bold]{k}[/bold] {v}")

    asyncio.run(_run())


@forensic_app.command("wipe")
def forensic_wipe(run_id: str) -> None:
    """Cryptographically shred a forensic capture."""
    _setup()
    if not typer.confirm(f"Wipe forensic capture {run_id!r}? This is permanent."):
        raise typer.Exit(code=0)

    async def _run() -> None:
        from spark.forensic.reader import ForensicRepository
        from spark.runtime import get_secret_manager

        await init_db()
        repo = ForensicRepository(get_secret_manager())
        ok = await repo.wipe(run_id)
        if not ok:
            err_console.print(f"capture {run_id!r} not found")
            raise typer.Exit(code=1)
        console.print(f"[green]wiped[/green] {run_id}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# doctor commands
# ---------------------------------------------------------------------------


@doctor_app.command("check")
def doctor_check() -> None:
    """Check that the host has a working sandbox backend and deps."""
    _setup()
    from spark.sandbox.executor import SandboxUnavailable, check_available

    try:
        backend = check_available()
        console.print(f"[green]sandbox backend[/green] {backend}")
    except SandboxUnavailable as exc:
        err_console.print(f"[red]sandbox unavailable[/red]: {exc}")
        raise typer.Exit(code=1) from exc

    try:
        import chromadb  # noqa: F401
        console.print("[green]chromadb[/green] ok")
    except ImportError:
        err_console.print("[yellow]chromadb not installed[/yellow]")

    try:
        import presidio_analyzer  # noqa: F401
        console.print("[green]presidio[/green] ok")
    except ImportError:
        err_console.print("[yellow]presidio not installed[/yellow]")


# ---------------------------------------------------------------------------
# skills commands
# ---------------------------------------------------------------------------


@skills_app.command("review")
def skills_review(
    agent_name: str | None = typer.Option(None, "--agent"),
) -> None:
    """List pending skill review queue."""
    _setup()

    async def _run() -> None:
        from spark.skills.catalog import SkillCatalog

        await init_db()
        pending = await SkillCatalog().list_pending(agent_name)
        table = Table("review_id", "agent", "service", "name", "confidence")
        for p in pending:
            table.add_row(
                p.review_id,
                p.agent_name,
                p.skill.service_name,
                p.skill.name,
                f"{p.confidence:.2f}",
            )
        console.print(table)

    asyncio.run(_run())


@skills_app.command("approve")
def skills_approve(
    review_id: str,
    reviewer: str = typer.Option(..., "--reviewer"),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    """Approve a pending skill review."""
    _setup()

    async def _run() -> None:
        from spark.skills.catalog import SkillCatalog
        from spark.skills.schemas import SkillReviewDecision

        await init_db()
        result = await SkillCatalog().decide(
            SkillReviewDecision(
                review_id=review_id,
                decision="approve",
                reviewer=reviewer,
                notes=notes,
            )
        )
        if result is None:
            err_console.print(f"review {review_id} not found")
            raise typer.Exit(1)
        console.print(f"[green]approved[/green] state={result.state}")

    asyncio.run(_run())


@skills_app.command("reject")
def skills_reject(
    review_id: str,
    reviewer: str = typer.Option(..., "--reviewer"),
    notes: str | None = typer.Option(None, "--notes"),
) -> None:
    _setup()

    async def _run() -> None:
        from spark.skills.catalog import SkillCatalog
        from spark.skills.schemas import SkillReviewDecision

        await init_db()
        result = await SkillCatalog().decide(
            SkillReviewDecision(
                review_id=review_id,
                decision="reject",
                reviewer=reviewer,
                notes=notes,
            )
        )
        if result is None:
            err_console.print(f"review {review_id} not found")
            raise typer.Exit(1)
        console.print(f"rejected state={result.state}")

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# serve — launch the web UI
# ---------------------------------------------------------------------------


@app.command("serve")
def serve(
    config_path: Path | None = typer.Option(
        None, "--config", "-c", help="Path to SparkRuntime YAML (default: ~/.spark/spark.yaml)"
    ),
    rotate_credentials: bool = typer.Option(
        False, "--rotate-credentials", help="Regenerate user+password this startup"
    ),
    rotate_token: bool = typer.Option(
        False, "--rotate-token", help="Regenerate the headless API token"
    ),
) -> None:
    """Launch the Spark web UI and JSON API."""
    import sys as _sys

    from spark.config.runtime_config import WebBindPublic
    from spark.runtime.bootstrap import bootstrap
    from spark.web.app import WebDisabled, build_app_with_auth, banner_for

    _setup()

    try:
        cfg = bootstrap(config_path)
    except Exception as exc:
        err_console.print(f"[red]config error:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not cfg.spec.web.enabled:
        err_console.print(
            "[red]web UI is disabled[/red]\n"
            "  Run [bold]spark config init[/bold] to create a SparkRuntime YAML,\n"
            "  then set [bold]spec.web.enabled: true[/bold] and pick a bind mode."
        )
        raise typer.Exit(1)

    try:
        app_instance, fresh_creds = build_app_with_auth(
            cfg,
            rotate_credentials=rotate_credentials or None,
            rotate_token=rotate_token,
        )
    except WebDisabled as exc:
        err_console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    bind = cfg.spec.web.bind
    console.print("[bold]Spark web[/bold]")
    scheme = "https" if isinstance(bind, WebBindPublic) else "http"
    console.print(f"  bind:  {scheme}://{bind.host}:{bind.port} (mode={bind.mode})")

    banner = banner_for(cfg, fresh_creds)
    if banner is not None:
        print(banner, file=_sys.stderr, flush=True)
    else:
        err_console.print(
            "[yellow]credentials persisted from previous run — pass "
            "--rotate-credentials to mint new ones[/yellow]"
        )

    if bind.mode != "loopback":
        err_console.print(
            f"[bold yellow]WARNING[/bold yellow] binding in '{bind.mode}' mode. "
            "Ensure your allowed_cidrs are correct."
        )

    try:
        import uvicorn  # type: ignore[import-not-found]
    except ImportError as exc:
        err_console.print(
            "uvicorn is not installed. pip install spark-runtime[web]"
        )
        raise typer.Exit(1) from exc

    uvicorn_kwargs: dict[str, object] = {
        "host": bind.host,
        "port": bind.port,
        "log_level": "info",
    }
    if isinstance(bind, WebBindPublic):
        uvicorn_kwargs["ssl_certfile"] = str(bind.tls.cert_file)
        uvicorn_kwargs["ssl_keyfile"] = str(bind.tls.key_file)

    uvicorn.run(app_instance, **uvicorn_kwargs)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# config subcommands
# ---------------------------------------------------------------------------


@config_app.command("init")
def config_init(
    path: Path | None = typer.Option(None, "--path"),
) -> None:
    """Write an example SparkRuntime YAML if one does not already exist."""
    from spark.config.runtime_config import DEFAULT_CONFIG_PATH, write_example

    target = write_example(path)
    console.print(f"[green]wrote[/green] {target}")
    console.print(
        "Edit the file, set [bold]spec.web.enabled: true[/bold], then run "
        "[bold]spark serve[/bold]."
    )


@config_app.command("show")
def config_show(
    path: Path | None = typer.Option(None, "--path"),
) -> None:
    """Print the parsed SparkRuntime config."""
    from spark.config.runtime_config import load_runtime

    cfg = load_runtime(path)
    console.print_json(cfg.model_dump_json(indent=2))


# ---------------------------------------------------------------------------
# daemon subcommands (wired from spark.daemon)
# ---------------------------------------------------------------------------


@daemon_app.command("install")
def daemon_install(
    config_path: Path | None = typer.Option(None, "--config", "-c"),
    dry_run: bool = typer.Option(False, "--dry-run"),
) -> None:
    from spark.config.runtime_config import load_runtime
    from spark.daemon.install import install_daemon

    cfg = load_runtime(config_path)
    if cfg.spec.daemon is None:
        err_console.print("[red]no spec.daemon block in config[/red]")
        raise typer.Exit(1)
    result = install_daemon(cfg, dry_run=dry_run)
    for line in result.lines:
        console.print(line)


@daemon_app.command("uninstall")
def daemon_uninstall(
    config_path: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    from spark.config.runtime_config import load_runtime
    from spark.daemon.install import uninstall_daemon

    cfg = load_runtime(config_path)
    if cfg.spec.daemon is None:
        err_console.print("[red]no spec.daemon block in config[/red]")
        raise typer.Exit(1)
    for line in uninstall_daemon(cfg).lines:
        console.print(line)


@daemon_app.command("start")
def daemon_start(
    config_path: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    from spark.config.runtime_config import load_runtime
    from spark.daemon.install import daemon_control

    cfg = load_runtime(config_path)
    if cfg.spec.daemon is None:
        err_console.print("[red]no spec.daemon block in config[/red]")
        raise typer.Exit(1)
    for line in daemon_control(cfg, "start").lines:
        console.print(line)


@daemon_app.command("stop")
def daemon_stop(
    config_path: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    from spark.config.runtime_config import load_runtime
    from spark.daemon.install import daemon_control

    cfg = load_runtime(config_path)
    if cfg.spec.daemon is None:
        err_console.print("[red]no spec.daemon block in config[/red]")
        raise typer.Exit(1)
    for line in daemon_control(cfg, "stop").lines:
        console.print(line)


@daemon_app.command("status")
def daemon_status(
    config_path: Path | None = typer.Option(None, "--config", "-c"),
) -> None:
    from spark.config.runtime_config import load_runtime
    from spark.daemon.install import daemon_control

    cfg = load_runtime(config_path)
    if cfg.spec.daemon is None:
        err_console.print("[red]no spec.daemon block in config[/red]")
        raise typer.Exit(1)
    for line in daemon_control(cfg, "status").lines:
        console.print(line)


# ---------------------------------------------------------------------------
# secrets subcommand (H1.3)
# ---------------------------------------------------------------------------


def _resolve_vault() -> "AgeFileVault":
    """Build the AgeFileVault from the current runtime config.

    We bypass the process-scoped manager here because some secrets
    commands (``init-age-vault``, ``rotate-vault-key``) need to talk
    to the vault directly, not through the dispatch layer.
    """
    from spark.config.runtime_config import load_runtime
    from spark.secrets import AgeFileVault, AgeVaultPaths

    cfg = load_runtime()
    paths = AgeVaultPaths(
        vault=Path(cfg.spec.secrets.age_file.vault_path).expanduser(),
        identity=Path(cfg.spec.secrets.age_file.identity_path).expanduser(),
        identity_wrapped=(
            Path(cfg.spec.secrets.age_file.identity_path).expanduser().with_name(
                Path(cfg.spec.secrets.age_file.identity_path).name + ".age"
            )
        ),
    )
    pw = None
    if cfg.spec.secrets.age_file.passphrase_env:
        import os as _os

        pw = _os.environ.get(cfg.spec.secrets.age_file.passphrase_env)
    return AgeFileVault(paths, passphrase=pw)


@secrets_app.command("list")
def secrets_list() -> None:
    """List secret names in the age vault (never values)."""
    _setup()
    from spark.secrets import VaultNotInitialized

    vault = _resolve_vault()
    try:
        vault.unlock()
    except VaultNotInitialized:
        err_console.print(
            "[red]no age vault found[/red]\n"
            "  Run [bold]spark secrets init-age-vault[/bold] first."
        )
        raise typer.Exit(1) from None

    names = vault.list_names()
    if not names:
        console.print("[dim](no secrets)[/dim]")
        return
    for name in names:
        console.print(f"  {name}")


@secrets_app.command("set")
def secrets_set(
    name: str = typer.Argument(..., help="Secret name (e.g. anthropic_key)"),
    value: str | None = typer.Option(
        None,
        "--value",
        help=(
            "Inline value (avoid in shell history). Omit to be prompted "
            "with a no-echo input."
        ),
    ),
) -> None:
    """Store a secret in the age vault (prompted; never echoes)."""
    _setup()
    from getpass import getpass

    from spark.secrets import VaultNotInitialized

    vault = _resolve_vault()
    try:
        vault.unlock()
    except VaultNotInitialized:
        err_console.print(
            "[red]no age vault found[/red] — run "
            "[bold]spark secrets init-age-vault[/bold] first."
        )
        raise typer.Exit(1) from None

    if value is None:
        value = getpass(f"Value for {name!r}: ")
    if not value:
        err_console.print("[red]empty value refused[/red]")
        raise typer.Exit(1)
    vault.set(name, value)
    console.print(f"[green]stored[/green] {name}")


@secrets_app.command("delete")
def secrets_delete(
    name: str = typer.Argument(..., help="Secret name"),
) -> None:
    """Delete a secret from the age vault."""
    _setup()
    from spark.secrets import VaultNotInitialized

    vault = _resolve_vault()
    try:
        vault.unlock()
    except VaultNotInitialized:
        err_console.print("[red]no age vault found[/red]")
        raise typer.Exit(1) from None

    if name not in vault.list_names():
        err_console.print(f"[yellow]no secret named {name!r}[/yellow]")
        raise typer.Exit(1)
    vault.delete(name)
    console.print(f"[green]deleted[/green] {name}")


@secrets_app.command("init-age-vault")
def secrets_init_age_vault(
    passphrase: bool = typer.Option(
        False,
        "--passphrase",
        help=(
            "Passphrase-wrap the identity. The daemon will read the "
            "passphrase from SPARK_AGE_PASSPHRASE, or prompt interactively."
        ),
    ),
    force: bool = typer.Option(
        False, "--force", help="Refuse to overwrite an existing vault (default)."
    ),
) -> None:
    """Create the age identity and encrypted vault.

    Normally the vault is auto-created by ``spark serve`` on first boot.
    Use this command when you want to pre-provision, passphrase-wrap, or
    rebuild from scratch (with ``--force``).
    """
    _setup()
    from getpass import getpass

    from spark.secrets import VaultAlreadyExists

    vault = _resolve_vault()
    if vault.is_initialized() and not force:
        err_console.print(
            "[red]vault already exists[/red] — pass [bold]--force[/bold] to "
            "rebuild. WARNING: rebuilding destroys all stored secrets."
        )
        raise typer.Exit(1)

    if force and vault.is_initialized():
        # Delete existing files so `init()` succeeds.
        for p in (vault.paths.vault, vault.paths.identity, vault.paths.identity_wrapped):
            if p.exists():
                p.unlink()

    pw: str | None = None
    if passphrase:
        pw = getpass("Passphrase (leave empty to abort): ")
        if not pw:
            err_console.print("[red]aborted[/red]")
            raise typer.Exit(1)
        confirm = getpass("Confirm passphrase: ")
        if confirm != pw:
            err_console.print("[red]passphrase mismatch[/red]")
            raise typer.Exit(1)

    try:
        vault.init(passphrase=pw)
    except VaultAlreadyExists:
        err_console.print("[red]vault already exists (race?)[/red]")
        raise typer.Exit(1) from None

    console.print(
        f"[green]initialized age vault[/green] at {vault.paths.vault}"
    )
    if pw:
        console.print(
            "  Identity is passphrase-wrapped. Set "
            "[bold]SPARK_AGE_PASSPHRASE[/bold] for unattended daemons, or "
            "expect an interactive prompt at startup."
        )
    else:
        console.print(
            "  Identity at [bold]{}[/bold] (mode 0600). "
            "Protect it with filesystem permissions.".format(vault.paths.identity)
        )


@secrets_app.command("rotate-vault-key")
def secrets_rotate_vault_key(
    passphrase: bool = typer.Option(
        False,
        "--passphrase",
        help=(
            "Keep the new identity passphrase-wrapped (same semantics as "
            "init-age-vault --passphrase). Omit for an unwrapped identity."
        ),
    ),
) -> None:
    """Generate a new age identity and re-encrypt the vault under it.

    The old identity is discarded. Cryptographic shredding of the old
    key is a file-level delete — if you need forensic-grade wipe, also
    ``shred`` the old identity file before running this command.
    """
    _setup()
    from getpass import getpass

    from spark.secrets import VaultNotInitialized

    vault = _resolve_vault()
    try:
        vault.unlock()
    except VaultNotInitialized:
        err_console.print("[red]no age vault found[/red]")
        raise typer.Exit(1) from None

    pw: str | None = None
    if passphrase:
        pw = getpass("New passphrase: ")
        if not pw:
            err_console.print("[red]aborted[/red]")
            raise typer.Exit(1)
        confirm = getpass("Confirm passphrase: ")
        if confirm != pw:
            err_console.print("[red]passphrase mismatch[/red]")
            raise typer.Exit(1)

    vault.rotate_identity(passphrase=pw)
    console.print("[green]rotated vault identity[/green]")


@secrets_app.command("healthcheck")
def secrets_healthcheck() -> None:
    """Probe the vault + env fallback and print a status report."""
    _setup()
    from spark.runtime import get_secret_manager
    from spark.secrets import VaultNotInitialized

    mgr = get_secret_manager()
    console.print("[bold]Secrets backends[/bold]")

    if mgr.vault is not None:
        try:
            mgr.vault.unlock()
        except VaultNotInitialized:
            console.print("  [yellow]age_file[/yellow] — not initialized")
        except Exception as exc:  # pragma: no cover
            console.print(f"  [red]age_file[/red] — error: {exc}")
        else:
            count = len(mgr.vault.list_names())
            console.print(
                f"  [green]age_file[/green] — ok ({count} secret(s))"
            )

    if mgr.env_fallback_enabled:
        env_count = len(mgr._env.list_names()) if mgr._env else 0  # type: ignore[union-attr]
        if env_count:
            console.print(
                f"  [yellow]env fallback[/yellow] — {env_count} "
                f"SPARK_SECRET_* var(s) present (consider migrating to the vault)"
            )
        else:
            console.print("  [dim]env fallback[/dim] — enabled, no vars set")
    else:
        console.print("  [dim]env fallback[/dim] — disabled")


# ---------------------------------------------------------------------------
# template subcommand (H1.1)
# ---------------------------------------------------------------------------


@template_app.command("list")
def template_list() -> None:
    """List the shipped agent templates."""
    _setup()
    from spark.templates import list_templates

    try:
        templates = list_templates()
    except Exception as exc:  # pragma: no cover
        err_console.print(f"[red]failed to load templates:[/red] {exc}")
        raise typer.Exit(1) from exc

    if not templates:
        console.print("[dim](no templates)[/dim]")
        return

    table = Table("name", "description", "plugins", "secrets")
    for t in templates:
        table.add_row(
            t.name,
            t.description[:60] + ("…" if len(t.description) > 60 else ""),
            ", ".join(t.plugins_required),
            ", ".join(t.secrets_required) or "—",
        )
    console.print(table)


@template_app.command("show")
def template_show(name: str = typer.Argument(...)) -> None:
    """Print a template's README + YAML files."""
    _setup()
    from spark.templates import TemplateNotFound, load_template

    try:
        tpl = load_template(name)
    except TemplateNotFound:
        err_console.print(f"[red]no template named {name!r}[/red]")
        raise typer.Exit(1) from None

    console.print(f"[bold]{tpl.name}[/bold]")
    console.print(tpl.readme)
    console.print("\n[bold]agent.yaml[/bold]")
    console.print(tpl.agent_yaml)
    console.print("\n[bold]task.yaml[/bold]")
    console.print(tpl.task_yaml)


@template_app.command("install")
def template_install(
    name: str = typer.Argument(...),
    overwrite: bool = typer.Option(
        False, "--overwrite", help="Replace existing files at the target paths."
    ),
) -> None:
    """Copy a template's agent + task YAMLs into ``~/.spark/``."""
    _setup()
    import shutil

    from spark.templates import TemplateNotFound, load_template

    try:
        tpl = load_template(name)
    except TemplateNotFound:
        err_console.print(f"[red]no template named {name!r}[/red]")
        raise typer.Exit(1) from None

    target_base = Path("~/.spark").expanduser()
    agents_dir = target_base / "agents"
    tasks_dir = target_base / "tasks"
    agents_dir.mkdir(parents=True, exist_ok=True)
    tasks_dir.mkdir(parents=True, exist_ok=True)

    agent_target = agents_dir / f"{tpl.name}.yaml"
    task_target = tasks_dir / f"{tpl.name}.yaml"

    if not overwrite and (agent_target.exists() or task_target.exists()):
        err_console.print(
            f"[red]refuses to overwrite existing files[/red] at:\n"
            f"  {agent_target}\n  {task_target}\n"
            f"Pass [bold]--overwrite[/bold] to replace."
        )
        raise typer.Exit(1)

    shutil.copyfile(tpl.directory / "agent.yaml", agent_target)
    shutil.copyfile(tpl.directory / "task.yaml", task_target)

    console.print(f"[green]installed[/green] template {tpl.name!r}")
    console.print(f"  agent: {agent_target}")
    console.print(f"  task:  {task_target}")

    if tpl.plugins_required:
        console.print(
            f"\n[bold]Plugins to configure:[/bold] {', '.join(tpl.plugins_required)}"
        )
        console.print("  (open the web UI's Plugins page to populate each one)")
    if tpl.secrets_required:
        console.print(
            f"\n[bold]Secrets to populate:[/bold] {', '.join(tpl.secrets_required)}"
        )
        console.print("  Run [bold]spark secrets set <name>[/bold] for each")


if __name__ == "__main__":
    app()
