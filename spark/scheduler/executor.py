"""Task execution glue — load YAMLs and invoke the lifecycle.

The web layer registers tasks (writes the YAML, upserts ``TaskRow``,
optionally adds an APScheduler job) but does not own a long-running
``Lifecycle`` instance the way the CLI does. This module provides the
shared "given a task name, run it" entry point used by:

  - ``SparkScheduler``'s default job_runner (cron / interval ticks)
  - ``POST /api/scheduler/trigger`` (manual fire from the web UI)
  - ``POST /api/scheduler/webhooks/{trigger_id}`` (external trigger)
  - ``Lifecycle.run_once``'s on_success / on_failure chain dispatch

It loads the on-disk YAMLs each call rather than caching, which keeps
the scheduler honest about edits made between fires and avoids holding
parsed state across restarts.

Chain depth is capped at ``CHAIN_DEPTH_CAP``. The chain is also checked
for repeats (cycle detection) before the next link fires.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from spark.logging import get_logger
from spark.persistence.db import session_scope
from spark.persistence.models import TaskRow

log = get_logger("spark.scheduler.executor")

CHAIN_DEPTH_CAP = 5


async def execute_task_by_name(
    task_name: str,
    *,
    triggered_by: str | None = None,
    payload: dict[str, Any] | None = None,
    triggered_by_chain: list[str] | None = None,
) -> None:
    """Load a task + its agent and run it once via the lifecycle.

    Errors are logged and swallowed — this is invoked from APScheduler
    ticks and detached background tasks where there is no caller to
    propagate to.

    ``payload`` lands on ``RunState.trigger_payload`` (the planner sees
    a fenced-JSON copy in its first system prompt; the unabridged copy
    is persisted on the run row).

    ``triggered_by_chain`` is the lineage of task names that led to
    this fire. Used by chain dispatch to refuse cycles and depth >
    cap. Web-UI / webhook / scheduler fires pass ``None`` (start
    fresh); ``Lifecycle.run_once`` extends it when dispatching
    ``on_success`` / ``on_failure``.
    """
    from spark.config.loader import load_agent, load_task
    from spark.plugins.registry import default_registry
    from spark.runtime import get_secret_manager
    from spark.runtime.lifecycle import Lifecycle

    chain = list(triggered_by_chain or [])
    if len(chain) >= CHAIN_DEPTH_CAP:
        log.warning(
            "executor.chain_depth_exceeded",
            task=task_name,
            chain=chain,
        )
        return
    if task_name in chain:
        log.warning("executor.chain_cycle", task=task_name, chain=chain)
        return

    async with session_scope() as session:
        row = await session.get(TaskRow, task_name)
        if row is None:
            log.warning("executor.task_missing", task=task_name)
            return
        task_config_path = row.config_path
        agent_name = row.agent_name

    if not task_config_path:
        log.warning("executor.no_config_path", task=task_name)
        return

    task_path = Path(task_config_path)
    agent_path = Path("~/.spark/agents").expanduser() / f"{agent_name}.yaml"

    if not task_path.exists():
        log.warning(
            "executor.task_yaml_missing", task=task_name, path=str(task_path)
        )
        return
    if not agent_path.exists():
        log.warning(
            "executor.agent_yaml_missing", task=task_name, path=str(agent_path)
        )
        return

    try:
        agent = load_agent(agent_path)
        task = load_task(task_path)
    except Exception as exc:
        log.warning(
            "executor.yaml_load_failed", task=task_name, error=str(exc)
        )
        return

    secrets = get_secret_manager()
    registry = default_registry()
    lifecycle = Lifecycle(secrets=secrets, registry=registry)

    # Lineage stamp: the chain plus this task's name. Stored in the run
    # row so chain inspection (and cycle checks across process restarts)
    # work without an extra table.
    extended_chain = [*chain, task_name]
    triggered_by_str = "|".join(
        [*([triggered_by] if triggered_by else []), *(f"task:{c}" for c in chain)]
    ) or triggered_by

    try:
        result = await lifecycle.run_once(
            agent,
            task,
            triggered_by=triggered_by_str,
            payload=payload,
            chain=extended_chain,
        )
        log.info(
            "executor.run_complete",
            task=task_name,
            run_id=result.run_id,
            state=result.state.value,
        )
    except Exception as exc:
        log.warning("executor.run_failed", task=task_name, error=str(exc))
