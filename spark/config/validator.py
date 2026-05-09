"""Cross-model validation beyond what Pydantic does alone.

Catches semantic errors that the schema can't express:
- recurring task without a schedule
- agent referencing unknown plugin in allowlist (checked at runtime load instead)
- filesystem permissions granted without fs.* grant
- http_client allowed without network.allow_hosts
"""

from __future__ import annotations

from dataclasses import dataclass

from spark.config.enums import Permission, TaskMode
from spark.config.models import Agent, Task


@dataclass(frozen=True)
class ValidationIssue:
    code: str
    message: str


def validate_agent(agent: Agent) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    spec = agent.spec

    if "http_client" in spec.plugins.allow and not spec.permissions.network.allow_hosts:
        issues.append(
            ValidationIssue(
                "http_client.no_hosts",
                "http_client is allowed but permissions.network.allow_hosts is empty",
            )
        )

    if "filesystem" in spec.plugins.allow and not spec.permissions.filesystem.allow_paths:
        issues.append(
            ValidationIssue(
                "filesystem.no_paths",
                "filesystem is allowed but permissions.filesystem.allow_paths is empty",
            )
        )

    if spec.logging.raw_prompts or spec.logging.raw_model_outputs:
        issues.append(
            ValidationIssue(
                "logging.raw_enabled",
                "raw prompt/output logging is enabled — this bypasses privacy defaults",
            )
        )

    # A grant for network should come with hosts.
    grants = set(spec.permissions.grants)
    if Permission.NET_HTTP in grants and not spec.permissions.network.allow_hosts:
        issues.append(
            ValidationIssue(
                "grant.net_http.no_hosts",
                "Permission.NET_HTTP granted but no hosts allowlisted",
            )
        )
    if (Permission.FS_READ in grants or Permission.FS_WRITE in grants) and not spec.permissions.filesystem.allow_paths:
        issues.append(
            ValidationIssue(
                "grant.fs.no_paths",
                "Filesystem grants without any allow_paths configured",
            )
        )

    return issues


def validate_task(task: Task) -> list[ValidationIssue]:
    issues: list[ValidationIssue] = []
    spec = task.spec

    if spec.mode in (TaskMode.RECURRING, TaskMode.PERPETUAL) and spec.schedule is None:
        issues.append(
            ValidationIssue(
                "schedule.required",
                f"Task mode {spec.mode.value!r} requires a schedule",
            )
        )
    if spec.mode == TaskMode.ONE_SHOT and spec.schedule is not None:
        issues.append(
            ValidationIssue(
                "schedule.unexpected",
                "One-shot tasks must not define a schedule",
            )
        )

    if spec.output.type == "file" and spec.output.path is None:
        issues.append(
            ValidationIssue(
                "output.path_required",
                "output.type=file requires output.path",
            )
        )

    return issues
