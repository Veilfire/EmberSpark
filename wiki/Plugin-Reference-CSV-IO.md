# Plugin Reference: `csv_io`

Typed, delimiter-aware, encoding-safe CSV read/write. Rows returned as dicts keyed by column name so the agent references columns symbolically.

- **Required permissions:** `fs.read`, `fs.write` (when `allow_write: true`)
- **Required secrets:** none
- **Sensitivity:** `MODERATE`
- **Network:** not needed
- **Dependencies:** stdlib `csv` only

---

## What the plugin does

Three operations:

- **`read`** — parse a CSV file into a list of dicts. Optionally headerless.
- **`write`** — write a new CSV (overwrites any existing file).
- **`append`** — append rows to an existing CSV, emitting a header row only on empty files.

Path validation uses `PathPolicy` (same semantics as `filesystem`).

---

## Configuration fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `allow_paths` | list of paths | `[]` | Directories the plugin may touch. **When empty**, falls back to the data-volume's `scratch` and `deliverables` paths (which are already sandbox-scoped to the agent's `fs.write` grant), so a fresh install can read+write CSVs in those locations immediately. Configure explicit paths for production. |
| `deny_paths` | list of paths | `[]` | Nested denies. |
| `max_rows_per_read` | int | `100_000` | Per-call row cap. Excess is truncated with `truncated=true`. |
| `max_cols` | int | `200` | Column-count ceiling. Rows with more columns are trimmed. |
| `max_cell_bytes` | int | `10_000` | Per-cell UTF-8 byte cap. |
| `default_encoding` | string | `utf-8` | Fallback when per-call `encoding` is omitted. |
| `allow_write` | bool | `true` | Master switch for `write` / `append`. |

---

## What the model sends per call

### Read

```json
{
  "op": "read",
  "path": "~/workspace/invoices.csv",
  "delimiter": ",",
  "has_header": true
}
```

Returns:

```json
{
  "op": "read",
  "path": "/home/me/workspace/invoices.csv",
  "columns": ["id", "date", "amount", "customer"],
  "rows": [
    {"id": "1", "date": "2026-04-01", "amount": "100.00", "customer": "ACME"},
    ...
  ],
  "row_count": 500,
  "truncated": false
}
```

### Write

```json
{
  "op": "write",
  "path": "~/workspace/report.csv",
  "delimiter": ",",
  "has_header": true,
  "columns": ["id", "status"],
  "rows": [
    {"id": "1", "status": "ok"},
    {"id": "2", "status": "failed"}
  ]
}
```

### Append

Same shape as `write`. The plugin emits a header row only if the target file is empty (zero bytes).

---

## Operator workflow

**Start read-only.** Set `allow_write: false` initially. Widen later if the agent has a legitimate write use case.

**Scratch + deliverables pairing.** Pair the `csv_io` `allow_paths` with the data volume's scratch directory for intermediate files and the deliverables directory for outputs the user should download. The sandbox auto-mounts both when `fs.write` is granted.

**Large files.** The default `max_rows_per_read: 100_000` is generous. For huge datasets, split into pages or move to `sqlite` instead — CSV is fine up to ~1M rows but the model can't really work with that many rows in its context anyway.

---

## Common pitfalls

- **Encoding mismatch** — Excel CSVs are often `cp1252` or `utf-16`. Pass `encoding: "cp1252"` explicitly in the per-call args; the plugin will honor it (subject to the operator's `default_encoding`).
- **Newlines inside cells** — the `csv` module handles quoted newlines correctly but your allowlisted path list should not include `~/.ssh/authorized_keys`-style files where the extension doesn't match.
- **Row dicts vs lists** — rows are dicts, always. If the CSV has duplicate column names, later values win.

---

## Further reading

- [Plugin Reference: filesystem](Plugin-Reference-Filesystem) — for binary reads
- [Plugin Reference: sqlite](Plugin-Reference-SQLite) — for larger structured datasets
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md)
