"""CSV read/write plugin.

Typed, delimiter-aware, encoding-safe structured data access. Narrow by
design: reads go through ``PathPolicy.check``, writes are gated on an
operator ``allow_write`` toggle, and all rows are returned as dicts of
strings so the agent references columns by name without guessing.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field

from spark.config.enums import Permission, Sensitivity


class CsvIoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_paths: list[Path] = Field(default_factory=list)
    deny_paths: list[Path] = Field(default_factory=list)
    max_rows_per_read: int = Field(default=100_000, ge=1, le=10_000_000)
    max_cols: int = Field(default=200, ge=1, le=10_000)
    max_cell_bytes: int = Field(default=10_000, ge=1, le=1_000_000)
    default_encoding: str = Field(default="utf-8", max_length=32)
    allow_write: bool = True
    #: OWASP CSV-injection mitigation. When True (default), cells whose
    #: first character is one of ``= + - @ \t \r`` are prefixed with a
    #: single quote at write time so that Excel / LibreOffice / Numbers
    #: refuse to interpret them as formulas. Operators can disable this
    #: if they specifically need to write literal leading-equals cells,
    #: but the default is safe.
    csv_injection_guard: bool = True


class CsvIoArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["read", "write", "append"] = Field(
        description="Operation: 'read' loads rows, 'write' replaces the file, 'append' adds rows.",
    )
    path: Path = Field(
        description="Target CSV path. Must be inside the operator's allow_paths.",
    )
    delimiter: str = Field(
        default=",",
        min_length=1,
        max_length=4,
        description="Column delimiter (default ','). Common alternatives: '\\t', '|', ';'.",
    )
    encoding: str | None = Field(
        default=None,
        max_length=32,
        description="Text encoding ('utf-8', 'latin-1', …). Defaults to operator config.",
    )
    has_header: bool = Field(
        default=True,
        description="When true, the first row is treated as column names and rows return as dicts.",
    )
    rows: list[dict[str, str]] | None = Field(
        default=None,
        description="Required for 'write' / 'append'. List of {column: value} dicts.",
    )
    columns: list[str] | None = Field(
        default=None,
        description="Optional column order for write/append. Inferred from first row when omitted.",
    )


class CsvReadResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["read"] = "read"
    path: str
    columns: list[str]
    rows: list[dict[str, str]]
    row_count: int
    truncated: bool


class CsvWriteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    op: Literal["write", "append"]
    path: str
    rows_written: int


class CsvIoPlugin:
    name: ClassVar[str] = "csv_io"
    version: ClassVar[str] = "0.1.0"
    description: ClassVar[str] = (
        "Read and write CSV files under an operator-allowlisted path tree. "
        "Rows are dicts keyed by column name."
    )
    input_schema: ClassVar[type[BaseModel]] = CsvIoArgs
    # The output schema is a discriminated union done by field presence.
    # We validate against a concrete shape by running through Pydantic
    # inside `execute` and returning the right model.
    output_schema: ClassVar[type[BaseModel]] = CsvReadResult
    config_schema: ClassVar[type[BaseModel]] = CsvIoConfig
    required_permissions: ClassVar[frozenset[Permission]] = frozenset(
        {Permission.FS_READ, Permission.FS_WRITE}
    )
    required_secrets: ClassVar[frozenset[str]] = frozenset()
    sensitivity: ClassVar[Sensitivity] = Sensitivity.MODERATE
    filter_output_before_model: ClassVar[bool] = True
    needs_network: ClassVar[bool] = False

    async def execute(self, args: CsvIoArgs, ctx: Any) -> Any:
        from spark.utils.paths import PathPolicy

        cfg = getattr(ctx, "plugin_config", {}) or {}
        allow_paths = [str(p) for p in (cfg.get("allow_paths") or [])]
        deny_paths = [str(p) for p in (cfg.get("deny_paths") or [])]
        if not allow_paths:
            # Working default: scratch + deliverables paths from the data
            # volume. Both are already scoped to the agent's grants by the
            # sandbox policy, so falling back here doesn't widen reach —
            # it just removes the "every read fails out of the box" footgun.
            for attr in ("scratch_path", "deliverables_path"):
                p = getattr(ctx, attr, None)
                if p:
                    allow_paths.append(str(p))
        max_rows = int(cfg.get("max_rows_per_read") or 100_000)
        max_cols = int(cfg.get("max_cols") or 200)
        max_cell_bytes = int(cfg.get("max_cell_bytes") or 10_000)
        default_encoding = cfg.get("default_encoding") or "utf-8"
        allow_write = bool(cfg.get("allow_write", True))
        csv_injection_guard = bool(cfg.get("csv_injection_guard", True))

        policy = PathPolicy.from_strings(allow_paths, deny_paths)
        resolved = policy.check(args.path)
        encoding = args.encoding or default_encoding

        if args.op == "read":
            return self._read(resolved, args.delimiter, encoding, args.has_header, max_rows, max_cols, max_cell_bytes)

        if args.op in ("write", "append"):
            if not allow_write:
                raise PermissionError("csv_io: allow_write is False; writes refused")
            if args.rows is None:
                raise PermissionError("csv_io: write requires a `rows` list")
            if args.columns is None and args.rows:
                # infer columns from the first row
                args_columns = list(args.rows[0].keys())
            else:
                args_columns = args.columns or []
            return self._write(
                resolved,
                args.delimiter,
                encoding,
                args.has_header,
                args_columns,
                args.rows,
                append=(args.op == "append"),
                injection_guard=csv_injection_guard,
            )

        raise PermissionError(f"csv_io: unknown op {args.op!r}")

    def _read(
        self,
        path: Path,
        delimiter: str,
        encoding: str,
        has_header: bool,
        max_rows: int,
        max_cols: int,
        max_cell_bytes: int,
    ) -> CsvReadResult:
        if not path.exists():
            raise FileNotFoundError(f"csv_io: {path} does not exist")
        rows: list[dict[str, str]] = []
        truncated = False
        with path.open("r", encoding=encoding, newline="") as f:
            reader = csv.reader(f, delimiter=delimiter)
            try:
                first = next(reader)
            except StopIteration:
                return CsvReadResult(
                    path=str(path), columns=[], rows=[], row_count=0, truncated=False
                )
            if len(first) > max_cols:
                raise PermissionError(
                    f"csv_io: {path} has {len(first)} columns (max {max_cols})"
                )
            if has_header:
                columns = [_trim(c, max_cell_bytes) for c in first]
                data_rows = reader
            else:
                columns = [f"col_{i}" for i in range(len(first))]
                # first line was data — push it into the stream
                data_rows = _prepend(first, reader)
            for raw in data_rows:
                if len(rows) >= max_rows:
                    truncated = True
                    break
                if len(raw) > max_cols:
                    raw = raw[:max_cols]
                rows.append(
                    {
                        columns[i] if i < len(columns) else f"col_{i}": _trim(
                            raw[i], max_cell_bytes
                        )
                        for i in range(len(raw))
                    }
                )
        return CsvReadResult(
            path=str(path),
            columns=columns,
            rows=rows,
            row_count=len(rows),
            truncated=truncated,
        )

    def _write(
        self,
        path: Path,
        delimiter: str,
        encoding: str,
        has_header: bool,
        columns: list[str],
        rows: list[dict[str, str]],
        *,
        append: bool,
        injection_guard: bool,
    ) -> CsvWriteResult:
        mode = "a" if append else "w"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open(mode, encoding=encoding, newline="") as f:
            writer = csv.DictWriter(f, fieldnames=columns, delimiter=delimiter)
            if (has_header and not append) or (has_header and append and path.stat().st_size == 0):
                writer.writeheader()
            for row in rows:
                safe_row = (
                    {k: _neutralize_formula(v) for k, v in row.items()}
                    if injection_guard
                    else row
                )
                writer.writerow(safe_row)
        return CsvWriteResult(
            op="append" if append else "write",
            path=str(path),
            rows_written=len(rows),
        )


# OWASP CSV-injection mitigation. Leading `=`, `+`, `-`, `@`, tab, or CR
# are the characters that Excel / LibreOffice / Numbers interpret as
# formula starters. Prefix with a single quote so the spreadsheet tool
# treats the cell as a literal.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _neutralize_formula(value: str) -> str:
    if not value:
        return value
    if value[0] in _FORMULA_PREFIXES:
        return "'" + value
    return value


def _trim(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _prepend(first_row: list[str], reader: Any):
    yield first_row
    yield from reader
