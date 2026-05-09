"""SQLite-backed session memory with FIFO eviction and summary-only writes."""

from __future__ import annotations

from dataclasses import dataclass

from spark.persistence.db import session_scope
from spark.persistence.models import SessionMemoryRow
from spark.persistence.repositories import SessionRepository


@dataclass
class SessionEntry:
    kind: str
    content: str


class SessionMemory:
    def __init__(self, *, session_id: str, session_name: str, agent_name: str, max_entries: int) -> None:
        self.session_id = session_id
        self.session_name = session_name
        self.agent_name = agent_name
        self.max_entries = max_entries

    async def ensure(self) -> None:
        async with session_scope() as session:
            repo = SessionRepository(session)
            await repo.ensure(
                session_id=self.session_id,
                name=self.session_name,
                agent_name=self.agent_name,
            )

    async def append(self, entry: SessionEntry) -> None:
        async with session_scope() as session:
            repo = SessionRepository(session)
            await repo.ensure(
                session_id=self.session_id,
                name=self.session_name,
                agent_name=self.agent_name,
            )
            await repo.append_memory(
                SessionMemoryRow(
                    session_id=self.session_id,
                    kind=entry.kind,
                    content=entry.content,
                )
            )
            await repo.prune(self.session_id, self.max_entries)

    async def recent(self, limit: int = 20) -> list[SessionEntry]:
        async with session_scope() as session:
            repo = SessionRepository(session)
            rows = await repo.list_memory(self.session_id, limit)
        return [SessionEntry(kind=r.kind, content=r.content) for r in reversed(rows)]
