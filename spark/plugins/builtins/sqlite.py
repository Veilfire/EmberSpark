"""SQLite plugin — operator-allowlisted databases with sqlglot pre-parse gate.

Design rules:
- Operator registers every allowed database by ``name`` with an explicit
  ``path`` and ``mode`` (``read`` | ``read_write``).
- The plugin never accepts a path from the model; only the ``name`` handle.
- SQL is parsed with ``sqlglot`` **before** execution. Statement types are
  gated by mode:
    - ``read`` — ``SELECT``, ``WITH`` only
    - ``read_write`` — ``SELECT``, ``INSERT``, ``UPDATE``, ``DELETE`` only
- ``ATTACH`` / ``DETACH`` / ``PRAGMA write_*`` / DDL are refused in any mode.
- Read-mode connections set ``PRAGMA query_only = ON`` as belt + suspenders.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity


class SqliteDatabase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    path: str
    mode: Literal["read", "read_write"] = "read"
    query_timeout_seconds: float = Field(default=2.0, gt=0, le=60)
    max_rows: int = Field(default=1000, ge=1, le=100_000)


class SqliteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    databases: list[SqliteDatabase] = Field(default_factory=list)


class SqliteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    database: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9._-]+$",
        description="Logical database name from the operator's databases mapping. Resolves to a file path.",
    )
    sql: str = Field(
        min_length=1,
        max_length=16_000,
        description="SQL statement. Pre-parsed by sqlglot; banned keywords (DROP, ATTACH, …) refused.",
    )
    params: list[str | int | float | bool | None] = Field(
        default_factory=list,
        max_length=64,
        description="Positional bind parameters for `?` placeholders. Use these instead of inlining values.",
    )


class SqliteRow(BaseModel):
    values: list[str | int | float | bool | None]


class SqliteResult(BaseModel):
    database: str
    statement_type: str
    columns: list[str]
    rows: list[SqliteRow]
    row_count: int
    truncated: bool


_READ_TYPES = {"SELECT", "WITH"}
_WRITE_TYPES = {"SELECT", "WITH", "INSERT", "UPDATE", "DELETE"}
# Explicit deny list for extra safety — sqlglot may not recognize some
# statement types uniformly across dialects.
_BANNED_KEYWORDS = (
    "ATTACH",
    "DETACH",
    "VACUUM",
    "PRAGMA",
    "CREATE",
    "DROP",
    "ALTER",
    "REINDEX",
    "ANALYZE",
)


def _classify_sql(sql: str) -> str:
    """Return the primary statement type. Raises ValueError on junk / multi-stmt."""
    import sqlglot
    from sqlglot import exp

    try:
        parsed = sqlglot.parse(sql, dialect="sqlite")
    except Exception as exc:  # pragma: no cover — lib raises generic
        raise ValueError(f"unparseable SQL: {exc}") from exc
    # Reject empty parse AND multi-statement scripts.
    statements = [s for s in parsed if s is not None]
    if len(statements) != 1:
        raise ValueError("exactly one SQL statement is required")

    stmt = statements[0]
    if isinstance(stmt, exp.Select):
        return "SELECT"
    if isinstance(stmt, exp.With):
        return "WITH"
    if isinstance(stmt, exp.Insert):
        return "INSERT"
    if isinstance(stmt, exp.Update):
        return "UPDATE"
    if isinstance(stmt, exp.Delete):
        return "DELETE"
    raise ValueError(f"unsupported statement: {type(stmt).__name__}")


class SqlitePlugin:
    name: ClassVar[str] = "sqlite"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Bounded SQLite access against an operator-allowlisted database set."
    )
    input_schema: ClassVar[type[BaseModel]] = SqliteArgs
    output_schema: ClassVar[type[BaseModel]] = SqliteResult
    config_schema: ClassVar[type[BaseModel]] = SqliteConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.FS_READ}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: SqliteArgs, ctx: Any) -> SqliteResult:
        plugin_config = getattr(ctx, "plugin_config", {}) or {}
        databases_raw = plugin_config.get("databases") or []
        databases: dict[str, SqliteDatabase] = {}
        for raw in databases_raw:
            db = SqliteDatabase.model_validate(raw)
            databases[db.name] = db

        if args.database not in databases:
            raise PermissionError(
                f"database {args.database!r} not in operator allowlist"
            )
        db = databases[args.database]

        # Quick banned-keyword check (case-insensitive).
        upper_sql = args.sql.upper()
        for banned in _BANNED_KEYWORDS:
            if banned in upper_sql:
                raise PermissionError(f"banned SQL keyword: {banned}")

        try:
            stmt_type = _classify_sql(args.sql)
        except ValueError as exc:
            raise PermissionError(str(exc)) from exc

        allowed_types = _WRITE_TYPES if db.mode == "read_write" else _READ_TYPES
        if stmt_type not in allowed_types:
            raise PermissionError(
                f"statement {stmt_type} not allowed in mode {db.mode!r}"
            )

        path = Path(db.path).expanduser()
        if not path.exists():
            raise PermissionError(f"database file {path} does not exist")

        # Execute in a thread so we don't block the event loop.
        def _run() -> tuple[list[str], list[tuple[Any, ...]], bool]:
            uri = (
                f"file:{path}?mode=ro"
                if db.mode == "read"
                else f"file:{path}?mode=rw"
            )
            conn = sqlite3.connect(
                uri,
                uri=True,
                timeout=db.query_timeout_seconds,
                isolation_level=None,
            )
            try:
                conn.execute("PRAGMA busy_timeout = 1000;")
                if db.mode == "read":
                    conn.execute("PRAGMA query_only = ON;")
                cursor = conn.execute(args.sql, list(args.params))
                cols = [d[0] for d in (cursor.description or [])]
                rows_out: list[tuple[Any, ...]] = []
                truncated = False
                for row in cursor:
                    if len(rows_out) >= db.max_rows:
                        truncated = True
                        break
                    rows_out.append(tuple(row))
                return cols, rows_out, truncated
            finally:
                conn.close()

        try:
            cols, raw_rows, truncated = await asyncio.wait_for(
                asyncio.to_thread(_run),
                timeout=db.query_timeout_seconds + 1.0,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"query on {args.database!r} exceeded "
                f"{db.query_timeout_seconds}s"
            ) from exc

        rows = [
            SqliteRow(values=[_coerce_cell(v) for v in row]) for row in raw_rows
        ]
        return SqliteResult(
            database=args.database,
            statement_type=stmt_type,
            columns=cols,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
        )


def _coerce_cell(v: Any) -> str | int | float | bool | None:
    if v is None or isinstance(v, (str, int, float, bool)):
        return v
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return str(v)
