"""Tests for the sqlite plugin gate (sqlglot pre-parse + mode enforcement)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from spark.plugins.builtins.sqlite import (
    SqliteArgs,
    SqliteConfig,
    SqliteDatabase,
    SqlitePlugin,
    _classify_sql,
)


class _Ctx:
    def __init__(self, config: dict) -> None:
        self.secrets: dict[str, str] = {}
        self.privacy_mode = "strict"
        self.plugin_config = config


def _mkdb(path: Path) -> None:
    conn = sqlite3.connect(str(path))
    try:
        conn.execute("CREATE TABLE things (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO things VALUES (1, 'one'), (2, 'two'), (3, 'three')")
        conn.commit()
    finally:
        conn.close()


def test_classify_select() -> None:
    assert _classify_sql("SELECT * FROM things") == "SELECT"


def test_classify_insert() -> None:
    assert _classify_sql("INSERT INTO things VALUES (4, 'four')") == "INSERT"


def test_classify_multi_stmt_rejected() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        _classify_sql("SELECT 1; SELECT 2")


def test_banned_keyword_rejected_by_pre_check() -> None:
    # Even though PRAGMA is not a sqlglot SELECT/etc, the upper-case
    # keyword check runs first.
    import asyncio

    plugin = SqlitePlugin()
    ctx = _Ctx(
        SqliteConfig(
            databases=[SqliteDatabase(name="x", path="/tmp/x.db", mode="read")]
        ).model_dump()
    )

    async def _run():
        with pytest.raises(PermissionError, match="banned SQL keyword"):
            await plugin.execute(
                SqliteArgs(database="x", sql="PRAGMA table_info(things)"), ctx
            )

    asyncio.get_event_loop().run_until_complete(_run())


@pytest.mark.asyncio
async def test_read_mode_select_succeeds(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    _mkdb(path)
    plugin = SqlitePlugin()
    ctx = _Ctx(
        SqliteConfig(
            databases=[SqliteDatabase(name="t", path=str(path), mode="read")]
        ).model_dump()
    )
    result = await plugin.execute(
        SqliteArgs(database="t", sql="SELECT id, name FROM things ORDER BY id"),
        ctx,
    )
    assert result.row_count == 3
    assert result.columns == ["id", "name"]
    assert result.rows[0].values == [1, "one"]


@pytest.mark.asyncio
async def test_read_mode_update_rejected(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    _mkdb(path)
    plugin = SqlitePlugin()
    ctx = _Ctx(
        SqliteConfig(
            databases=[SqliteDatabase(name="t", path=str(path), mode="read")]
        ).model_dump()
    )
    with pytest.raises(PermissionError, match="not allowed in mode"):
        await plugin.execute(
            SqliteArgs(database="t", sql="UPDATE things SET name='x' WHERE id=1"),
            ctx,
        )


@pytest.mark.asyncio
async def test_unknown_database_rejected(tmp_path: Path) -> None:
    plugin = SqlitePlugin()
    ctx = _Ctx(
        SqliteConfig(
            databases=[
                SqliteDatabase(name="allowed", path=str(tmp_path / "x.db"), mode="read")
            ]
        ).model_dump()
    )
    with pytest.raises(PermissionError, match="not in operator allowlist"):
        await plugin.execute(
            SqliteArgs(database="totally-other", sql="SELECT 1"), ctx
        )
