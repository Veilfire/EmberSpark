"""Forensic writer — encrypts and persists one snapshot at a time.

Ownership model:

- The engine constructs one :class:`ForensicWriter` per task run when
  ``spec.forensic.enabled`` is true.
- The writer generates a per-run X25519 identity, stores the **private**
  key as a secret under ``forensic:<run_id>`` via the secrets vault,
  and retains the public **recipient** in memory for encryption.
- Every ``record_*`` call dumps its Pydantic payload, encrypts it to
  the recipient, and inserts one row into ``forensic_snapshots``.
- ``wipe()`` deletes the private key first (cryptographic shred), then
  the snapshot rows, then the capture row.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from spark.forensic.schemas import (
    ForensicMemorySnapshot,
    ForensicModelSnapshot,
    ForensicPromptSnapshot,
    ForensicReflectionSnapshot,
    ForensicSnapshotKind,
    ForensicToolSnapshot,
)
from spark.logging import get_logger
from spark.persistence.db import session_scope
from spark.persistence.learning_models import (
    ForensicCaptureRow,
    ForensicSnapshotRow,
)
from spark.utils.time import utcnow

if TYPE_CHECKING:  # pragma: no cover
    from spark.secrets import SecretManager

log = get_logger("spark.forensic")


def _vault_key_for(run_id: str) -> str:
    return f"forensic:{run_id}"


class ForensicWriter:
    """Per-run forensic capture writer."""

    def __init__(
        self,
        *,
        run_id: str,
        agent_name: str,
        task_name: str,
        enabled_by: str,
        enabled_reason: str,
        ttl_hours: int,
        secrets: "SecretManager",
    ) -> None:
        self.run_id = run_id
        self.agent_name = agent_name
        self.task_name = task_name
        self.enabled_by = enabled_by
        self.enabled_reason = enabled_reason
        self.ttl_hours = ttl_hours
        self.secrets = secrets

        self._identity_str: str | None = None
        self._recipient_obj: Any = None
        self._sequence: int = 0
        self._iteration_count: int = 0
        self._snapshot_count: int = 0
        self._started: bool = False

    async def start(self) -> None:
        """Generate the per-run identity and write the capture row.

        Idempotent: calling ``start()`` twice on the same instance is a
        no-op. Raises if pyrage is missing or the secrets vault rejects
        the write — forensic capture is opt-in, so failing closed here
        means the run simply reports the failure and continues without
        forensic coverage.
        """
        if self._started:
            return

        try:
            from pyrage import x25519
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "pyrage is required for forensic capture; install spark[full]"
            ) from exc

        identity = x25519.Identity.generate()
        recipient = identity.to_public()
        identity_str = str(identity)

        # Persist the private identity in the vault. Deleting this secret
        # is the cryptographic-shred operation.
        self.secrets.set(_vault_key_for(self.run_id), identity_str)

        self._identity_str = identity_str
        self._recipient_obj = recipient

        expires_at = utcnow() + timedelta(hours=self.ttl_hours)

        async with session_scope() as session:
            row = ForensicCaptureRow(
                run_id=self.run_id,
                agent_name=self.agent_name,
                task_name=self.task_name,
                enabled_by=self.enabled_by,
                enabled_reason=self.enabled_reason,
                captured_at=utcnow(),
                expires_at=expires_at,
                vault_key=_vault_key_for(self.run_id),
                iteration_count=0,
                snapshot_count=0,
            )
            session.add(row)

        self._started = True
        log.info(
            "forensic.started",
            run_id=self.run_id,
            agent=self.agent_name,
            task=self.task_name,
            ttl_hours=self.ttl_hours,
        )

    # ------------------------------------------------------------------
    # Recording — one method per snapshot kind. All share the same seq.
    # ------------------------------------------------------------------

    async def record_prompt(
        self,
        *,
        iteration: int,
        system_prompt: str,
        user_message: str | None,
        memory_context: list[dict[str, Any]] | None = None,
        playbook_id: str | None = None,
        message_count: int = 0,
        span_id: int | None = None,
    ) -> None:
        if not self._started:
            return
        snap = ForensicPromptSnapshot(
            iteration=iteration,
            sequence=self._next_sequence(),
            span_id=span_id,
            system_prompt=system_prompt,
            user_message=user_message,
            memory_context=memory_context or [],
            playbook_id=playbook_id,
            char_count=len(system_prompt) + (len(user_message) if user_message else 0),
            message_count=message_count,
        )
        await self._persist(iteration, snap)

    async def record_model(
        self,
        *,
        iteration: int,
        provider: str,
        model: str,
        content: str,
        reasoning_blocks: list[dict[str, Any]] | None = None,
        tool_calls_requested: list[dict[str, Any]] | None = None,
        stop_reason: str | None = None,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        span_id: int | None = None,
    ) -> None:
        if not self._started:
            return
        snap = ForensicModelSnapshot(
            iteration=iteration,
            sequence=self._next_sequence(),
            span_id=span_id,
            provider=provider,
            model=model,
            content=content,
            reasoning_blocks=reasoning_blocks or [],
            tool_calls_requested=tool_calls_requested or [],
            stop_reason=stop_reason,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )
        await self._persist(iteration, snap)

    async def record_tool(
        self,
        *,
        iteration: int,
        plugin: str,
        args: dict[str, Any],
        raw_result: Any = None,
        filtered_result: Any = None,
        redactions: list[str] | None = None,
        error_code: str | None = None,
        error_detail: dict[str, Any] | None = None,
        duration_seconds: float | None = None,
        span_id: int | None = None,
    ) -> None:
        if not self._started:
            return
        snap = ForensicToolSnapshot(
            iteration=iteration,
            sequence=self._next_sequence(),
            span_id=span_id,
            plugin=plugin,
            args=args,
            raw_result=raw_result,
            filtered_result=filtered_result,
            redactions=redactions or [],
            error_code=error_code,
            error_detail=error_detail,
            duration_seconds=duration_seconds,
        )
        await self._persist(iteration, snap)

    async def record_memory(
        self,
        *,
        iteration: int,
        direction: str,
        memory_ids: list[str],
        records: list[dict[str, Any]] | None = None,
        span_id: int | None = None,
    ) -> None:
        if not self._started:
            return
        kind = (
            ForensicSnapshotKind.MEMORY_WRITTEN
            if direction == "written"
            else ForensicSnapshotKind.MEMORY_RETRIEVED
        )
        snap = ForensicMemorySnapshot(
            iteration=iteration,
            sequence=self._next_sequence(),
            span_id=span_id,
            kind=kind,
            direction=direction,
            memory_ids=memory_ids,
            records=records or [],
        )
        await self._persist(iteration, snap)

    async def record_reflection(
        self,
        *,
        iteration: int,
        summary: str,
        lessons: list[str] | None = None,
        patterns: list[str] | None = None,
        follow_ups: list[str] | None = None,
        span_id: int | None = None,
    ) -> None:
        if not self._started:
            return
        snap = ForensicReflectionSnapshot(
            iteration=iteration,
            sequence=self._next_sequence(),
            span_id=span_id,
            summary=summary,
            lessons=lessons or [],
            patterns=patterns or [],
            follow_ups=follow_ups or [],
        )
        await self._persist(iteration, snap)

    async def finalize(self) -> None:
        """Record the final iteration/snapshot counts on the capture row."""
        if not self._started:
            return
        async with session_scope() as session:
            row = await session.get(ForensicCaptureRow, self.run_id)
            if row is not None:
                row.iteration_count = self._iteration_count
                row.snapshot_count = self._snapshot_count
                session.add(row)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    async def _persist(self, iteration: int, payload: Any) -> None:
        try:
            from pyrage import encrypt
        except ImportError as exc:  # pragma: no cover
            log.warning("forensic.pyrage_missing", error=str(exc))
            return

        if self._recipient_obj is None:
            return

        json_bytes = payload.model_dump_json().encode("utf-8")
        try:
            ciphertext = encrypt(json_bytes, [self._recipient_obj])
        except Exception as exc:  # pragma: no cover — best effort
            log.warning("forensic.encrypt_failed", error=str(exc), run_id=self.run_id)
            return

        if iteration > self._iteration_count:
            self._iteration_count = iteration
        self._snapshot_count += 1

        async with session_scope() as session:
            row = ForensicSnapshotRow(
                run_id=self.run_id,
                iteration=iteration,
                sequence=payload.sequence,
                span_id=payload.span_id,
                kind=payload.kind.value,
                captured_at=utcnow(),
                payload_encrypted=ciphertext,
            )
            session.add(row)
