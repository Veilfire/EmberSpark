"""Playbook entity + store.

A `Playbook` is a named procedure the agent has successfully run before. The
store is a thin façade over `PlaybookRepository` that keeps the transactional
surface clean for the engine's prepare-context and post-run hooks.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

from spark.learning.bandit import (
    BanditScore,
    select_playbook,
    update_beta_posterior,
)
from spark.learning.fingerprint import compute_fingerprint
from spark.persistence.db import session_scope
from spark.persistence.learning_models import PlaybookRow, PlaybookRunRow
from spark.persistence.learning_repos import PlaybookRepository
from spark.utils.hashing import sha256_text
from spark.utils.ids import short_id


@dataclass
class Playbook:
    playbook_id: str
    agent_name: str
    name: str
    description: str
    fingerprint: str
    tool_sequence: list[str]
    alpha: float
    beta: float
    uses: int
    avg_duration_seconds: float
    avg_tool_calls: float
    avg_model_calls: float
    last_success_at: datetime | None

    @property
    def success_rate(self) -> float:
        total = self.alpha + self.beta
        return float(self.alpha / total) if total > 0 else 0.5

    def summary(self) -> str:
        return (
            f"{self.name}: {self.description} "
            f"(uses={self.uses}, success={self.success_rate:.2f}, "
            f"tools={','.join(self.tool_sequence)})"
        )


@dataclass
class PlaybookCandidate:
    name: str
    description: str
    objective_hint: str
    tool_sequence: list[str]


def _row_to_playbook(row: PlaybookRow) -> Playbook:
    tool_seq = [t for t in row.tool_sequence.split("|") if t]
    return Playbook(
        playbook_id=row.playbook_id,
        agent_name=row.agent_name,
        name=row.name,
        description=row.description,
        fingerprint=row.fingerprint,
        tool_sequence=tool_seq,
        alpha=row.alpha,
        beta=row.beta,
        uses=row.uses,
        avg_duration_seconds=row.avg_duration_seconds,
        avg_tool_calls=row.avg_tool_calls,
        avg_model_calls=row.avg_model_calls,
        last_success_at=row.last_success_at,
    )


class PlaybookStore:
    """Persistent playbook library with bandit-driven selection."""

    async def list_for_agent(self, agent_name: str) -> list[Playbook]:
        async with session_scope() as session:
            repo = PlaybookRepository(session)
            rows = await repo.list_for_agent(agent_name)
        return [_row_to_playbook(r) for r in rows]

    async def find_applicable(
        self,
        *,
        agent_name: str,
        objective: str,
        available_tools: Iterable[str],
        max_candidates: int = 5,
    ) -> list[Playbook]:
        """Return playbooks that could plausibly apply to this objective.

        We use two gating heuristics:
          1. The playbook's tool_sequence must be a subset of `available_tools`.
          2. At least one token in the objective must overlap the playbook's
             fingerprint-producing normalized form (cheap Jaccard screen).

        The final ranking (bandit selection) happens in `select_for_run`.
        """
        available = set(available_tools)
        candidates = await self.list_for_agent(agent_name)

        obj_tokens = set(objective.lower().split())
        fit: list[tuple[float, Playbook]] = []
        for pb in candidates:
            if not set(pb.tool_sequence).issubset(available):
                continue
            pb_tokens = set(pb.name.lower().split() + pb.description.lower().split())
            if not pb_tokens:
                overlap = 0.0
            else:
                overlap = len(obj_tokens & pb_tokens) / max(1, len(obj_tokens | pb_tokens))
            if overlap < 0.05 and pb.uses < 1:
                continue
            fit.append((overlap, pb))

        fit.sort(key=lambda t: (t[0], t[1].success_rate), reverse=True)
        return [pb for _, pb in fit[:max_candidates]]

    async def select_for_run(
        self,
        *,
        agent_name: str,
        objective: str,
        available_tools: Iterable[str],
    ) -> Playbook | None:
        applicable = await self.find_applicable(
            agent_name=agent_name,
            objective=objective,
            available_tools=available_tools,
        )
        if not applicable:
            return None
        scores = [
            BanditScore(
                playbook_id=pb.playbook_id,
                sampled_value=0.0,
                alpha=pb.alpha,
                beta=pb.beta,
                uses=pb.uses,
                applicability=pb.success_rate,
            )
            for pb in applicable
        ]
        winner = select_playbook(scores)
        if winner is None:
            return None
        return next((pb for pb in applicable if pb.playbook_id == winner.playbook_id), None)

    async def record_outcome(
        self,
        *,
        playbook_id: str,
        run_id: str,
        success: bool,
        duration_seconds: float,
        tool_calls: int,
        model_calls: int,
    ) -> None:
        async with session_scope() as session:
            repo = PlaybookRepository(session)
            pb = await repo.get(playbook_id)
            if pb is None:
                return
            alpha, beta = update_beta_posterior(pb.alpha, pb.beta, success)
            pb.alpha = alpha
            pb.beta = beta
            pb.uses += 1
            now_row = PlaybookRunRow(
                playbook_id=playbook_id,
                run_id=run_id,
                success=success,
                duration_seconds=duration_seconds,
                tool_calls=tool_calls,
                model_calls=model_calls,
            )
            # Exponential moving average keeps the running stats stable.
            ema = 0.3
            pb.avg_duration_seconds = (1 - ema) * pb.avg_duration_seconds + ema * duration_seconds
            pb.avg_tool_calls = (1 - ema) * pb.avg_tool_calls + ema * tool_calls
            pb.avg_model_calls = (1 - ema) * pb.avg_model_calls + ema * model_calls
            if success:
                pb.last_success_at = now_row.recorded_at
            await repo.record_run(now_row)
            await repo.upsert(pb)

    async def upsert_from_candidate(
        self,
        *,
        agent_name: str,
        candidate: PlaybookCandidate,
    ) -> Playbook:
        fingerprint = compute_fingerprint(
            candidate.objective_hint, candidate.tool_sequence
        )
        playbook_id = f"pb-{short_id()}-{sha256_text(fingerprint)[:10]}"
        async with session_scope() as session:
            repo = PlaybookRepository(session)
            existing = await repo.find_by_fingerprint(agent_name, fingerprint)
            if existing is not None:
                return _row_to_playbook(existing)
            row = PlaybookRow(
                playbook_id=playbook_id,
                agent_name=agent_name,
                name=candidate.name,
                description=candidate.description,
                fingerprint=fingerprint,
                applicability_summary=candidate.objective_hint,
                tool_sequence="|".join(candidate.tool_sequence),
            )
            await repo.upsert(row)
        return _row_to_playbook(row)
