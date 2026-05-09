"""Async SQLite engine + session lifecycle.

All Spark persistence flows through this module. We enable WAL mode, foreign
keys, and a sensible busy timeout. There is exactly one engine per process.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel

from spark.persistence import learning_models as _learning  # noqa: F401  — register tables
from spark.persistence import models as _models  # noqa: F401  — register tables
from spark.persistence.types import promote_datetime_columns

# Swap every naive SQLAlchemy DateTime column for the UTC-aware
# TypeDecorator. Must run before engine creation / create_all so reflected
# DDL carries the decorator and loaded rows come back tz-aware.
promote_datetime_columns(SQLModel.metadata)


LEGACY_DB_PATH = Path("~/.spark/spark.db").expanduser()


def _enable_pragmas(dbapi_connection, connection_record) -> None:  # type: ignore[no-untyped-def]
    cur = dbapi_connection.cursor()
    cur.execute("PRAGMA journal_mode=WAL;")
    cur.execute("PRAGMA foreign_keys=ON;")
    cur.execute("PRAGMA busy_timeout=5000;")
    cur.execute("PRAGMA synchronous=NORMAL;")
    cur.close()


_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def resolve_db_path(explicit: Path | None = None) -> Path:
    """Pick the effective SQLite path.

    Priority:
    1. An explicit ``db_path`` argument wins (used by tests).
    2. If a data volume is active AND ``sqlite_on_volume: true``, use
       ``data_volume.sqlite_path``.
    3. Otherwise fall back to the legacy ``~/.spark/spark.db``.
    """
    if explicit is not None:
        return explicit.expanduser()
    # Local import to avoid a cycle — runtime_config is pure, but
    # persistence.db is imported very early.
    from spark.config.runtime_config import get_data_volume

    dv = get_data_volume()
    if dv is not None and dv.sqlite_on_volume and dv.sqlite_path is not None:
        return dv.sqlite_path
    return LEGACY_DB_PATH


def get_engine(db_path: Path | None = None) -> AsyncEngine:
    global _engine, _session_factory
    if _engine is not None:
        return _engine
    path = resolve_db_path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f"sqlite+aiosqlite:///{path}"
    _engine = create_async_engine(url, future=True)
    event.listen(_engine.sync_engine, "connect", _enable_pragmas)
    _session_factory = async_sessionmaker(_engine, expire_on_commit=False, class_=AsyncSession)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if _session_factory is None:
        get_engine()
    assert _session_factory is not None
    return _session_factory


async def init_db(db_path: Path | None = None) -> None:
    """Create all tables + apply forward-only additive column migrations."""
    engine = get_engine(db_path)
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
        await conn.run_sync(_apply_additive_migrations)


def _apply_additive_migrations(sync_conn: Any) -> None:
    """Add missing columns to existing tables (SQLite, no Alembic).

    Safe because every new column has a default. Skips columns that
    already exist (fresh DBs won't trigger any ALTER TABLE).
    """
    from sqlalchemy import text  # noqa: PLC0415

    # Desired new columns on long_term_memory_index (M1 memory enhancements).
    wanted = [
        ("usage_count", "INTEGER NOT NULL DEFAULT 0"),
        ("successful_citation_count", "INTEGER NOT NULL DEFAULT 0"),
        ("last_cited_at", "DATETIME"),
        ("contradicts_with", "TEXT"),
        ("superseded_by", "TEXT"),
        ("provenance_json", "TEXT"),
        ("valid_from", "DATETIME"),
        ("valid_until", "DATETIME"),
        ("alpha", "REAL NOT NULL DEFAULT 1.0"),
        ("beta", "REAL NOT NULL DEFAULT 1.0"),
        ("status", "TEXT NOT NULL DEFAULT 'active'"),
        ("circle_id", "TEXT"),
        ("is_anti_pattern", "INTEGER NOT NULL DEFAULT 0"),
        ("consensus_sources", "TEXT"),
    ]
    try:
        existing = {
            row[1]
            for row in sync_conn.execute(
                text("PRAGMA table_info('long_term_memory_index')")
            ).fetchall()
        }
        for col_name, col_sql in wanted:
            if col_name not in existing:
                sync_conn.execute(
                    text(
                        f"ALTER TABLE long_term_memory_index ADD COLUMN {col_name} {col_sql}"
                    )
                )
    except Exception:  # pragma: no cover — non-SQLite backends or first boot
        pass

    # Additive migration for notification_preferences. Adds new per-kind
    # toggles so upgraded DBs gain them without a user re-init.
    pref_columns = [
        ("data_class_blocked", "INTEGER NOT NULL DEFAULT 1"),
        ("data_class_grant_expiring", "INTEGER NOT NULL DEFAULT 1"),
    ]
    try:
        existing = {
            row[1]
            for row in sync_conn.execute(
                text("PRAGMA table_info('notification_preferences')")
            ).fetchall()
        }
        for col_name, col_sql in pref_columns:
            if col_name not in existing:
                sync_conn.execute(
                    text(
                        f"ALTER TABLE notification_preferences ADD COLUMN {col_name} {col_sql}"
                    )
                )
    except Exception:  # pragma: no cover
        pass

    # Additive migration for task_runs — surface model output + trigger
    # payload to the run replay UI without forcing a fresh DB.
    run_columns = [
        ("result_text", "TEXT"),
        ("trigger_payload_json", "TEXT"),
    ]
    try:
        existing = {
            row[1]
            for row in sync_conn.execute(
                text("PRAGMA table_info('task_runs')")
            ).fetchall()
        }
        for col_name, col_sql in run_columns:
            if col_name not in existing:
                sync_conn.execute(
                    text(f"ALTER TABLE task_runs ADD COLUMN {col_name} {col_sql}")
                )
    except Exception:  # pragma: no cover
        pass

    # Additive migration for triggers — webhook auth modes + lockout.
    trigger_columns = [
        ("auth_mode", "TEXT NOT NULL DEFAULT 'bearer'"),
        ("secret_name", "TEXT"),
        ("payload_forwarding", "INTEGER NOT NULL DEFAULT 0"),
        ("event_filter_json", "TEXT"),
        ("failed_verify_count", "INTEGER NOT NULL DEFAULT 0"),
        ("locked_until", "DATETIME"),
        ("body_parser", "TEXT NOT NULL DEFAULT 'json'"),
    ]
    try:
        existing = {
            row[1]
            for row in sync_conn.execute(
                text("PRAGMA table_info('triggers')")
            ).fetchall()
        }
        for col_name, col_sql in trigger_columns:
            if col_name not in existing:
                sync_conn.execute(
                    text(f"ALTER TABLE triggers ADD COLUMN {col_name} {col_sql}")
                )
    except Exception:  # pragma: no cover
        pass


async def dispose() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        await session.commit()
    except Exception:
        await session.rollback()
        raise
    finally:
        await session.close()
