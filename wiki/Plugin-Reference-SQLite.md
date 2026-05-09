# Plugin Reference: `sqlite`

Bounded SQLite access against an operator-allowlisted database set. SQL statements are **pre-parsed with `sqlglot`** and gated by the per-database mode (`read` vs `read_write`) before they ever touch the database file.

- **Required permissions:** `fs.read`
- **Required secrets:** none
- **Sensitivity:** `MODERATE`
- **Network:** not needed

---

## What the plugin does

- Lets the operator register a named set of databases (`name`, `path`, `mode`, timeout, row cap)
- Accepts a model-supplied SQL string plus parameters
- Runs the SQL through `sqlglot.parse()` to classify the statement type
- Gates the statement type by the per-database mode
- Executes via `sqlite3` with a strict query timeout and `PRAGMA query_only` in read mode
- Returns the rows, columns, and metadata in a strict Pydantic schema

The plugin **never** takes a database path from the model. The model can only reference a database by the symbolic name the operator configured.

---

## What the model can do per call

```json
{
  "database": "notes",
  "sql": "SELECT id, title FROM entries WHERE created_at > ?",
  "params": ["2026-01-01"]
}
```

- `database` — must match a name in the operator config; any other name is refused
- `sql` — the statement to run (max 16 KB)
- `params` — positional parameters for `?` placeholders (max 64)

Returns:

```json
{
  "database": "notes",
  "statement_type": "SELECT",
  "columns": ["id", "title"],
  "rows": [
    {"values": [1, "first note"]},
    {"values": [2, "second note"]}
  ],
  "row_count": 2,
  "truncated": false
}
```

---

## Configuration fields

### `databases` — list of `SqliteDatabase` *(required)*

The operator-approved database registry. Each entry has:

#### SqliteDatabase fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | required | Symbolic handle (must match `^[a-zA-Z0-9._-]+$`) — the model references this, not the path. |
| `path` | path | required | Absolute path to the SQLite file on the host filesystem. |
| `mode` | `read` or `read_write` | `read` | Statement types allowed. See gate details below. |
| `query_timeout_seconds` | float | `2.0` | Per-query timeout. Connection `busy_timeout` is 1000 ms. |
| `max_rows` | int | `1000` | Hard ceiling on rows returned. Excess is truncated. |

Example:

```json
{
  "databases": [
    {
      "name": "notes",
      "path": "/home/jes/Documents/notes/index.db",
      "mode": "read",
      "query_timeout_seconds": 2.0,
      "max_rows": 500
    },
    {
      "name": "scratch",
      "path": "/home/jes/.spark-scratch.db",
      "mode": "read_write",
      "query_timeout_seconds": 5.0,
      "max_rows": 5000
    }
  ]
}
```

---

## The sqlglot gate

Every SQL string passes through four checks **before** execution:

### 1. Banned keyword prefilter

The SQL is uppercased and checked for these substrings:

- `ATTACH` / `DETACH` — prevents attaching other database files
- `VACUUM` — prevents rewriting the file
- `PRAGMA` — prevents changing connection-level settings (including `query_only`, `journal_mode`, etc.)
- `CREATE` / `DROP` / `ALTER` — no DDL
- `REINDEX` / `ANALYZE` — no maintenance commands

If any of these appear anywhere in the SQL (even inside a string literal), the call is refused. This is a belt; the next check is the suspender.

### 2. Parse with sqlglot

`sqlglot.parse(sql, dialect="sqlite")` parses the SQL. If the parser returns zero statements or more than one, the call is refused — **multi-statement scripts are not allowed**.

### 3. Statement classification

The single parsed statement is classified by its sqlglot expression type:

| sqlglot type | EmberSpark type |
|---|---|
| `Select` | `SELECT` |
| `With` | `WITH` |
| `Insert` | `INSERT` |
| `Update` | `UPDATE` |
| `Delete` | `DELETE` |
| anything else | **refused** |

### 4. Mode gate

The classified type is compared to the database's mode:

| Mode | Allowed types |
|---|---|
| `read` | `SELECT`, `WITH` |
| `read_write` | `SELECT`, `WITH`, `INSERT`, `UPDATE`, `DELETE` |

Mismatch → `PermissionError`.

### 5. Read-mode connection hardening

When `mode: read`, the connection is opened via SQLite URI `file:<path>?mode=ro` **and** `PRAGMA query_only = ON;` is executed immediately. Either would be enough; both together make sure the database file is untouched.

---

## Safety properties

- **Paths come from operator config only.** The model supplies the symbolic `name`, the operator supplies the `path`. The model cannot reference a database the operator hasn't approved.
- **The EmberSpark DB should never be in the allowlist.** Don't register `~/.spark/spark.db` — exposing the runtime's own state to the model is a bad idea.
- **Read mode cannot write.** Both URI `mode=ro` and `PRAGMA query_only = ON` are set. Even if the sqlglot gate had a false negative, the database file is opened read-only at the SQLite layer.
- **Row count cap.** `max_rows` truncates the cursor so a runaway query (e.g. `SELECT * FROM huge_table`) doesn't fill memory.
- **Query timeout.** `query_timeout_seconds` is enforced both by SQLite's `busy_timeout` and by `asyncio.wait_for` on the outer thread call. A long-running query is killed.
- **Parameterized queries.** `params` is passed as positional parameters to `conn.execute(sql, params)` — no string interpolation, no SQL injection via parameters.
- **Multi-statement refused.** No `SELECT 1; DROP TABLE users`. The sqlglot parse step enforces exactly-one-statement.

---

## Operator workflows

### Workflow 1 — A read-only index lookup

You have a SQLite index of notes at `/home/jes/notes.db`. You want the agent to search it but never modify it.

```json
{
  "databases": [
    {
      "name": "notes",
      "path": "/home/jes/notes.db",
      "mode": "read",
      "query_timeout_seconds": 2.0,
      "max_rows": 500
    }
  ]
}
```

The agent can now issue:

```sql
SELECT id, title, tags FROM notes WHERE title LIKE ? ORDER BY updated_at DESC LIMIT 20
```

…and get back up to 500 rows (truncated if the agent somehow supplies a query without LIMIT).

Any attempt at `INSERT` / `UPDATE` / `DELETE` / `ATTACH` / `PRAGMA` / `CREATE` is refused at the gate.

### Workflow 2 — A writable scratch space

Agent needs a place to store intermediate state between runs. Give it a dedicated scratch DB in read_write mode:

```json
{
  "databases": [
    {
      "name": "scratch",
      "path": "/home/jes/.spark-scratch.db",
      "mode": "read_write",
      "query_timeout_seconds": 5.0,
      "max_rows": 5000
    }
  ]
}
```

**You** pre-create the schema with `sqlite3 /home/jes/.spark-scratch.db "CREATE TABLE items (id INTEGER PRIMARY KEY, content TEXT)"` — the plugin will never issue DDL, so the agent can't create tables itself.

Then the agent can `INSERT`, `UPDATE`, `DELETE`, and `SELECT` against existing tables.

### Workflow 3 — Multiple databases, different modes

```json
{
  "databases": [
    {
      "name": "readonly-index",
      "path": "/var/lib/myapp/index.db",
      "mode": "read",
      "query_timeout_seconds": 3.0
    },
    {
      "name": "writable-log",
      "path": "/home/jes/.spark-log.db",
      "mode": "read_write",
      "query_timeout_seconds": 2.0,
      "max_rows": 10000
    }
  ]
}
```

The agent references each by name; you can have one permanent read-only source of truth and one per-agent scratch space without worrying about them getting confused.

---

## Common failures and what they mean

### `PermissionError: database 'notes' not in operator allowlist`

The model referenced a database name that isn't in `databases`. Either add it or ignore the attempt.

### `PermissionError: banned SQL keyword: PRAGMA`

The SQL string contains a banned keyword. This is the prefilter catching it. Rewrite the query to avoid the keyword.

### `PermissionError: exactly one SQL statement is required`

The model sent multiple statements (or an empty string, or junk). The plugin requires exactly one parseable statement. If the model wants to chain operations, it has to make separate tool calls.

### `PermissionError: statement UPDATE not allowed in mode 'read'`

You opened the database in read mode and the model tried to write. Either the agent's task is misconfigured (it shouldn't be writing) or the database should be `read_write`.

### `PermissionError: unsupported statement: Pragma`

sqlglot parsed the statement but it's not one of `SELECT/WITH/INSERT/UPDATE/DELETE`. EmberSpark refuses anything else even in `read_write` mode — no DDL, no PRAGMA, no ATTACH.

### `TimeoutError: query on 'notes' exceeded 2.0s`

The query took longer than `query_timeout_seconds`. Either raise the timeout (if the query is legitimately slow) or narrow the query (if it's sloppy).

### `PermissionError: database file /path/to/db does not exist`

You registered a path that isn't there. Check the path in the plugin config.

---

## Further reading

- [Using Plugins](Using-Plugins) — the operator workflow
- [Plugin Reference: filesystem](Plugin-Reference-Filesystem) — for reading SQLite files as raw bytes (when you need to)
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md) — source-level reference
