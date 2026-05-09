# Built-in Plugin Configuration Reference

This is the complete per-plugin reference for every field you can edit in the **Plugins** page of the EmberSpark Web UI. Pair this with [tools-and-permissions.md](tools-and-permissions.md) for the mental model.

Plugin config lives in the `plugin_configs` SQLite table, is DB-backed, and is edited exclusively through the UI (or the `/api/plugin-config/{name}` endpoint). Every write is audited at `elevated` severity and requires a `reason`.

---

## How to edit a config

1. Start the web UI: `spark serve`.
2. Sign in with the credentials printed on startup.
3. Click **Plugins** in the sidebar (or `cmd+K` → "Plugins").
4. Pick the plugin from the left sidebar list.
5. Edit the form fields on the right. Each field maps to one property in the plugin's `config_schema` and is rendered as a typed form input.
6. Type a **reason** in the "Reason" field — it's required and recorded in the audit log.
7. Click **Save**. The new config takes effect on the next tool call — no restart.

To revert to defaults, click **Reset to defaults** and the `plugin_configs` row is dropped. The next tool call auto-seeds a fresh row from the schema defaults.

> **The model sees this config.** At every model invocation, the runtime renders the operator-stored config for each allowlisted plugin into the system prompt under an "Operator config (effective for this run)" block. That block surfaces fields whose names suggest they gate behavior — `allow_paths`, `allow_hosts`, `rules`, `allowed_methods`, `enabled`, `provider`, `databases`, `allow_repos`, `allow_chat_ids`, etc. — and skips noise like `user_agent` / timeouts. Result: the agent picks argument values inside the actual constraints on the first try, instead of guessing common conventions and tripping `PATH_DENIED` / `URL_DENIED`. Both task runs and chat sessions go through the same surfacing path, so an edit you save here applies to both immediately.

---

## `filesystem`

Reads, writes, lists, and stats files inside an operator-allowlisted path tree.

### Config fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `allow_paths` | list of paths | `[]` | Directories the plugin is allowed to touch. `~` expands to the EmberSpark user's home. Resolved once at startup; symlinks are followed. |
| `deny_paths` | list of paths | `["~/.ssh", "~/.aws", "~/.config"]` | Directories that are refused even if they're nested inside an `allow_paths` entry. Applied after `allow_paths`. |
| `max_read_bytes` | int | `5_000_000` | Hard ceiling on any single `read` call. Larger files are truncated with `truncated=true` on the result. |
| `max_files_per_call` | int | `256` | Maximum entries returned by a `list` call. |
| `read_only` | bool | `false` | **Operator-only master switch.** When `true`, any `write` / `append` operation raises `PermissionError` before touching the disk. |

### What the model can override

The model's per-call args can pass `allow_paths`, `deny_paths`, `max_read_bytes`, `max_files_per_call` — but because these fields are in `config_schema`, **the operator's value wins** on overlap. In practice: set these in the UI and the model cannot widen them.

### Sensitivity

`MODERATE` — tool output is run through the privacy filter before reaching the model, which can strip large binary blobs and scrub anything that looks like a secret.

### Required permissions

`fs.read`, `fs.write`, `fs.list`

### Operator workflow

**Start narrow.** A common layout:

```json
{
  "allow_paths": ["~/Documents/spark-workspace"],
  "deny_paths": ["~/Documents/spark-workspace/.private"],
  "max_read_bytes": 1000000,
  "max_files_per_call": 128,
  "read_only": false
}
```

**Read-only mode for exploration.** Flip `read_only: true` when you want the agent to be able to inspect a workspace without modifying anything. Useful for code review agents or debugging an unfamiliar state.

**Deny sensitive subtrees.** Even inside a granted workspace, you can carve out areas the plugin can't reach. The resolved path comparison is applied **after** symlink resolution, so `~/Documents/spark-workspace/symlink-to-etc` doesn't bypass it.

---

## `http_client`

SSRF-hardened outbound HTTPS client with IP-pinning and mandatory host allowlist.

### Config fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `allow_hosts` | list of strings | `[]` | The complete list of hosts the plugin may reach. Every request's URL is IDN-normalized to punycode and compared against this list. Empty list = plugin is effectively disabled. |
| `allow_http` | bool | `false` | Permit `http://` URLs. Off by default because HTTPS is cheap and plaintext is a footgun. |
| `allowed_methods` | list of `GET/POST/PUT/DELETE` | `["GET"]` | **Operator-only.** The set of HTTP methods the plugin will actually issue. Any other method is refused inside the plugin. |
| `max_response_bytes` | int | `5_000_000` | Streaming byte counter aborts the response when exceeded; caller receives `truncated=true`. |
| `connect_timeout_seconds` | float | `5.0` | Socket connect timeout. |
| `read_timeout_seconds` | float | `15.0` | Per-chunk read timeout. |
| `user_agent` | string | `spark-runtime/0.1` | **Operator-only.** The `User-Agent` header added to every request. |

### What the model can override

The model may set `method`, `url`, `headers`, `body`, `json`, and `secret_headers` per call. `allow_hosts`, `allow_http`, `max_response_bytes`, timeouts, and `user_agent` are operator-controlled and the operator wins on overlap.

### Sensitivity

`MODERATE` — responses are filtered through the privacy pipeline before the model sees them.

### Required permissions

`net.http`, `secrets.read`

### Operator workflow

**Lock the hosts.** This is the single most important knob. If the agent is supposed to call only the GitHub API:

```json
{
  "allow_hosts": ["api.github.com"],
  "allow_http": false,
  "allowed_methods": ["GET"],
  "max_response_bytes": 5000000,
  "connect_timeout_seconds": 5.0,
  "read_timeout_seconds": 15.0,
  "user_agent": "my-org-research-bot/1.0"
}
```

**Methods default to GET only.** If the agent needs to create issues, add `POST` — but do so deliberately. The model cannot add `POST` to the list; only the operator can.

**Secret headers.** If a host needs auth, the model passes `secret_headers: {"Authorization": "Bearer <secret-ref>"}` — where `<secret-ref>` is a secret name the plugin will resolve at runtime. The secret must be declared in `required_secrets` on the plugin call and must exist in the age vault (populate with `spark secrets set <name>`). The cleartext secret never appears in logs, never reaches the model.

**Internal-IP access.** By default the http_client refuses any RFC1918 / loopback / link-local IP via the SSRF defense. If you genuinely need it (e.g. calling a homelab service), use the Security Center → Network → Internal IP grants flow, which requires typed agent-name confirmation, a TTL, and lands in the critical audit log.

---

## `markdown_writer`

Writes `.md` / `.markdown` files. A thin wrapper over the filesystem plugin with an extension restriction.

### Config fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `allow_paths` | list of paths | `[]` | Same semantics as the filesystem plugin. |
| `deny_paths` | list of paths | `[]` | Same semantics. |
| `allow_append` | bool | `true` | **Operator-only.** When `false`, `mode=append` is rejected — all writes must be full-file overwrites. |
| `allow_overwrite` | bool | `true` | **Operator-only.** When `false`, `mode=write` is rejected — only appends allowed. |

### What the model can override

`path`, `content`, `mode`. Paths are gated by both `allow_paths` / `deny_paths` and the `.md` / `.markdown` extension check, which happens at the Pydantic validator before the plugin runs.

### Sensitivity

`LOW`

### Required permissions

`fs.write`

### Operator workflow

**Append-only mode for journaling.** If an agent is supposed to produce a running log that can never rewrite past entries, set `allow_overwrite: false`. This prevents the model from accidentally (or deliberately) stomping prior content.

**Write-only for reports.** Inversely, if the agent produces reports that should always be fresh, set `allow_append: false`.

**Separate workspace from filesystem plugin.** You can have the filesystem plugin pointed at one workspace (read-only, for inspection) and markdown_writer pointed at another (write-enabled, for output). Two plugins, two separate config rows.

---

## `shell`

Argv-only subprocess execution with a per-command allowlist. **Ships disabled with an empty command set.** You must explicitly enable it and add each command before the plugin is usable.

### Config fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `enabled` | bool | `false` | Master switch. Even if the plugin is in the agent allowlist with subprocess grant, nothing runs until this is `true`. |
| `allowed_commands` | dict of string → ShellCommandSpec | `{}` | The command registry. Keys are symbolic names the model references; values define the allowed argv prefix, flags, positional count, etc. |

### ShellCommandSpec fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `argv_prefix` | list of strings | required | The fixed argv prefix for this command. E.g. `["git", "log", "--oneline"]`. Must have at least one element. |
| `allowed_flags` | list of strings | `[]` | Flags the model is allowed to append. Anything not in this list is refused. |
| `allowed_positional_count` | int | `0` | Maximum number of positional arguments the model can supply. |
| `max_stdout_bytes` | int | `1_000_000` | Hard ceiling on stdout. Excess is truncated with `truncated=true`. |
| `timeout_seconds` | int | `10` | Wall-clock timeout. The plugin kills the child process on timeout. |
| `cwd_must_be_in` | list of paths | `[]` | If non-empty, the `cwd` the model supplies must resolve to a descendant of one of these. If empty, `cwd` must be omitted. |

### What the model sends per call

```json
{
  "command": "git-log",
  "flags": ["-n", "10"],
  "positional": ["main"],
  "cwd": "~/workspace/myrepo"
}
```

### Sensitivity

`MODERATE`

### Required permissions

`subprocess`

### Operator workflow

**Start with a one-command allowlist.** If the agent needs `git log`, configure only that:

```json
{
  "enabled": true,
  "allowed_commands": {
    "git-log": {
      "argv_prefix": ["git", "log", "--oneline", "--no-color"],
      "allowed_flags": ["-n", "--since", "--until"],
      "allowed_positional_count": 1,
      "max_stdout_bytes": 500000,
      "timeout_seconds": 5,
      "cwd_must_be_in": ["~/workspace"]
    }
  }
}
```

The plugin builds argv as `argv_prefix + model_flags + model_positional` and runs it via `create_subprocess_exec` (never `shell=True`, never `os.system`). Shell metacharacters (`;`, `&`, `|`, backtick, `$`, newline) in any positional argument are rejected before the process is spawned.

**Per-command flag allowlists are strict.** The model can't pass `-z` if you didn't list it. If the model tries, the call is refused at the plugin layer and the attempt appears in the audit log.

**Multiple named variants of the same base command.** If you need both `git-log` and `git-status`, register them as two separate keys with different `argv_prefix` values. The model references the symbolic name, so the naming is up to you.

**Set `cwd_must_be_in` to constrain working directory.** Otherwise the plugin runs in the sandbox's default cwd, which may not be where the agent thinks it is.

**Never add `bash`, `sh`, or any interpreter.** The whole point is to avoid shell interpretation. If the agent needs a compound operation, register it as a named command with a fixed argv.

---

## `sqlite`

Bounded SQLite read/write against an operator-allowlisted database set. SQL is pre-parsed with `sqlglot` before execution and gated by mode.

### Config fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `databases` | list of SqliteDatabase | `[]` | The operator-approved database registry. Each entry has its own mode. |

### SqliteDatabase fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `name` | string | required | Symbolic handle the model references. Must match `^[a-zA-Z0-9._-]+$`. |
| `path` | path | required | Absolute path to the SQLite file on the host. |
| `mode` | `read` or `read_write` | `read` | Read mode allows `SELECT` / `WITH` only. Read-write mode additionally allows `INSERT`, `UPDATE`, `DELETE`. |
| `query_timeout_seconds` | float | `2.0` | Query timeout. The connection is opened with `busy_timeout=1000` as well. |
| `max_rows` | int | `1000` | Hard ceiling on rows returned. Excess is dropped with `truncated=true`. |

### What the model sends per call

```json
{
  "database": "notes",
  "sql": "SELECT id, title FROM entries WHERE created_at > ?",
  "params": ["2026-01-01"]
}
```

### The sqlglot gate

Every SQL string passes through these checks before execution:

1. **Banned-keyword prefilter.** Any SQL containing the upper-case tokens `ATTACH`, `DETACH`, `VACUUM`, `PRAGMA`, `CREATE`, `DROP`, `ALTER`, `REINDEX`, `ANALYZE` is refused outright.
2. **Statement classification.** `sqlglot.parse(sql, dialect="sqlite")` runs. If the parse returns zero or more than one statement, the call is refused (no multi-statement scripts).
3. **Mode gate.** The classified statement type is compared to the mode's allowed set. `read` allows `SELECT` and `WITH`. `read_write` adds `INSERT`, `UPDATE`, `DELETE`.
4. **Read-mode PRAGMA.** In read mode, the connection is opened as `file:<path>?mode=ro` and additionally executes `PRAGMA query_only = ON;` as belt + suspenders.

### Sensitivity

`MODERATE`

### Required permissions

`fs.read`

### Operator workflow

**Never allow writes to the EmberSpark DB.** Don't register `~/.spark/spark.db` as a database — even as `read` mode, exposing your own runtime state to the model is a bad idea.

**Start in read mode.** Always. Flip to `read_write` only after you've seen the agent's behavior against a real read-only copy.

**Per-file timeouts.** For small reference databases, 2 seconds is generous. For large analytical databases, you may need to raise `query_timeout_seconds`. Don't go above 30 — long queries lock the connection and tie up the sandbox child.

**Example: a read-only notes index.**

```json
{
  "databases": [
    {
      "name": "notes",
      "path": "/home/jes/Documents/notes/index.db",
      "mode": "read",
      "query_timeout_seconds": 2.0,
      "max_rows": 500
    }
  ]
}
```

**Example: a writable scratch DB.**

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

The model can now run `INSERT` / `UPDATE` / `DELETE` against `scratch` but not `CREATE TABLE` — table creation requires operator intervention via the CLI.

---

## `web_search`

Provider-agnostic web search. Picks one of five providers and normalizes the response.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `provider` | `brave` \| `serper` \| `tavily` \| `ddg_html` \| `bing` | `brave` | Which provider's API to call. `ddg_html` scrapes the no-auth DuckDuckGo HTML and does not need a key. |
| `api_key_secret` | string | `web_search_key` | Name of the age-vault secret holding the provider API key. Ignored for `ddg_html`. |
| `max_results` | int | `10` | Upper bound on results returned per call. |
| `safe_search` | `off` \| `moderate` \| `strict` | `moderate` | Provider-specific safe-search level. |
| `connect_timeout_seconds` | float | `5.0` | |
| `read_timeout_seconds` | float | `15.0` | |
| `user_agent` | string | `spark-runtime-web-search/0.1` | |

**Required permissions:** `net.http`, `secrets.read`. **Sensitivity:** MODERATE.

---

## `http_tool`

Per-host method matrix HTTP client with optional readable-content extraction on GET HTML responses. Where `http_client` has one flat `allow_hosts` × `allowed_methods` product, `http_tool` lets each host have its own method allowlist, response cap, and extraction behavior.

### Top-level fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `rules` | list of `HttpToolHostRule` | `[]` | One entry per host. No matching entry = call refused. |
| `default_max_response_bytes` | int | `10_000_000` | Per-rule override wins. |
| `default_connect_timeout_seconds` | float | `5.0` | |
| `default_read_timeout_seconds` | float | `15.0` | |
| `user_agent` | string | `spark-runtime-http-tool/0.1` | Operator-only. |

### `HttpToolHostRule` fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `host` | string | required | Exact FQDN. IDN-normalized at match time. |
| `allowed_methods` | list of `GET/POST/PUT/DELETE/PATCH/HEAD` | `["GET"]` | The model's per-call method must be in this list. |
| `allow_http` | bool | `false` | Per-host plaintext override. |
| `max_response_bytes` | int | `null` | Fall back to `default_max_response_bytes`. |
| `connect_timeout_seconds` | float | `null` | Fall back to default. |
| `read_timeout_seconds` | float | `null` | Fall back to default. |
| `extract_main_content` | bool | `false` | On GET HTML responses, run trafilatura readability extraction and return the article text in `main_content`. |
| `note` | string | `null` | Operator-only rationale, shows up in the audit trail. |

**Required permissions:** `net.http`, `secrets.read`. **Sensitivity:** MODERATE.

### Example

```json
{
  "rules": [
    {"host": "api.github.com", "allowed_methods": ["GET", "POST", "PUT", "DELETE", "PATCH"]},
    {"host": "api.stripe.com", "allowed_methods": ["GET", "POST"]},
    {"host": "news.ycombinator.com", "allowed_methods": ["GET"], "extract_main_content": true}
  ],
  "user_agent": "my-org-bot/1.0"
}
```

---

## `pdf_reader`

Extract text (and optional metadata) from PDF files under an operator-allowlisted path tree. Pure offline, no network.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `allow_paths` | list of paths | `[]` | Directories the plugin may open PDFs from. |
| `deny_paths` | list of paths | `[]` | Nested denies inside allow paths. |
| `max_pages` | int | `200` | Pages per call ceiling. |
| `max_chars_per_page` | int | `20_000` | Per-page truncation cap. |
| `include_metadata` | bool | `true` | Return PDF metadata (title, author, created_at). |

**Required permissions:** `fs.read`. **Sensitivity:** MODERATE. Uses `pypdf`.

---

## `datetime`

Date/time utilities. The strictest possible sandbox — no network, no filesystem, no secrets.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `default_timezone` | string | `UTC` | IANA timezone name used when the per-call arg is `null`. |
| `allow_arbitrary_timezones` | bool | `true` | If `false`, the model can only pick from `allowed_timezones`. |
| `allowed_timezones` | list of strings | `[]` | Operator allowlist when `allow_arbitrary_timezones=false`. |

**Required permissions:** none. **Sensitivity:** LOW.

Supported ops: `now`, `parse`, `add`, `diff`, `to_timezone`, `is_dst`.

---

## `csv_io`

Read + write CSV files. Rows returned as dicts so the agent references columns by name.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `allow_paths` | list of paths | `[]` | Same semantics as `filesystem`. |
| `deny_paths` | list of paths | `[]` | |
| `max_rows_per_read` | int | `100_000` | Per-call ceiling. |
| `max_cols` | int | `200` | Schema cap — calls with more columns are refused. |
| `max_cell_bytes` | int | `10_000` | Per-cell truncation. |
| `default_encoding` | string | `utf-8` | Fallback when per-call `encoding` is omitted. |
| `allow_write` | bool | `true` | Master switch for write/append ops. |

**Required permissions:** `fs.read`, `fs.write` (if `allow_write: true`). **Sensitivity:** MODERATE.

---

## `email_sender`

SMTP send-only with operator-locked sender, recipient domain allowlist, and attachment path gating.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `smtp_host` | string | required | SMTP server hostname. |
| `smtp_port` | int | `587` | |
| `use_starttls` | bool | `true` | |
| `username_secret` | string | `smtp_username` | Keyring secret for SMTP username. |
| `password_secret` | string | `smtp_password` | Keyring secret for SMTP password. |
| `from_address` | email | required | The envelope sender. Operator-locked — the model cannot override. |
| `allowed_to_domains` | list of strings | `[]` | If non-empty, every recipient's domain must be in the list. |
| `max_subject_chars` | int | `200` | |
| `max_body_chars` | int | `100_000` | |
| `max_recipients` | int | `10` | |
| `allow_html` | bool | `false` | When `false`, HTML bodies are refused. |
| `allow_attachments` | bool | `true` | |
| `attachment_allow_paths` | list of paths | `[]` | Attachments must live under one of these (typically scratch or deliverables). |
| `max_attachment_bytes` | int | `10_000_000` | |

**Required permissions:** `net.http`, `secrets.read`, `fs.read`. **Sensitivity:** HIGH.

---

## `git`

Narrow git operations on operator-allowlisted repos. Structured output per op.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `allow_repos` | list of paths | `[]` | Repo roots the plugin may operate on. |
| `allow_write` | bool | `false` | When `false`, only `status`/`log`/`diff`/`show`/`branch` are allowed. |
| `max_log_entries` | int | `500` | |
| `max_diff_bytes` | int | `1_000_000` | |
| `max_status_entries` | int | `500` | |
| `git_binary` | string | `git` | Defaults to the binary on PATH. |
| `timeout_seconds` | int | `30` | Per-op timeout. |

**Required permissions:** `fs.read`, `subprocess` (and `fs.write` when `allow_write=true`). **Sensitivity:** MODERATE.

Supported ops: `status`, `log`, `diff`, `show`, `branch`, `add`, `commit`.

---

## `json_query`

JMESPath filter over JSON payloads. Lets the agent extract specific fields from large API responses instead of feeding the whole blob to the model.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `max_input_bytes` | int | `5_000_000` | Input JSON size cap. |
| `max_output_chars` | int | `50_000` | Result text cap. |

**Required permissions:** none. **Sensitivity:** MODERATE (output mirrors input). Uses `jmespath`.

---

## `rss_reader`

Fetch + parse RSS/Atom feeds. Distinct from the scheduler's `http_new_row` event source — this is a tool the agent calls during a run.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `allow_hosts` | list of strings | `[]` | SSRF-defense allowlist (same as `http_client`). |
| `max_items` | int | `50` | |
| `include_content` | bool | `true` | Return parsed content body in each item. |
| `max_content_chars` | int | `5_000` | Per-item content truncation. |
| `connect_timeout_seconds` | float | `5.0` | |
| `read_timeout_seconds` | float | `15.0` | |
| `user_agent` | string | `spark-runtime-rss/0.1` | |

**Required permissions:** `net.http`. **Sensitivity:** MODERATE. Uses `feedparser`.

---

## `image_gen`

Provider-agnostic image generation. Writes output files to the data volume's deliverables directory so they appear in the Downloads page and trigger a notification.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `provider` | `openai` \| `stability` \| `replicate` | `openai` | Which provider's image API to call. |
| `api_key_secret` | string | `image_gen_key` | Keyring secret for the provider API key. |
| `default_model` | string | `dall-e-3` | Per-call can override. |
| `default_size` | `512x512` \| `1024x1024` \| `1792x1024` \| `1024x1792` | `1024x1024` | |
| `max_prompt_chars` | int | `4000` | |
| `max_images_per_call` | int | `4` | |
| `output_format` | `png` \| `webp` \| `jpeg` | `png` | |
| `connect_timeout_seconds` | float | `10.0` | |
| `read_timeout_seconds` | float | `60.0` | Image generation is slow; the default is generous. |
| `subdirectory` | string | `generated` | Subdirectory inside `deliverables_path` where images are written. |

**Required permissions:** `net.http`, `secrets.read`, `fs.write`. **Sensitivity:** MODERATE.

**Requires:** a populated data volume (`spec.data_volume.enabled: true` in `SparkRuntime`). The plugin refuses to run if `ctx.deliverables_path` is `None`.

---

## Defaults seen on first boot

On first start of the web UI, every registered plugin gets a row in `plugin_configs` auto-seeded from the Pydantic schema defaults. That means:

- `filesystem`, `http_client`, `markdown_writer` — seeded with empty allowlists. **Effectively unusable** until you configure them.
- `shell` — seeded with `enabled: false` and empty `allowed_commands`. Cannot run anything.
- `sqlite` — seeded with empty `databases`. Cannot query anything.
- `web_search`, `http_tool`, `pdf_reader`, `csv_io`, `email_sender`, `git`, `rss_reader`, `image_gen` — seeded empty. Configure before use.
- `datetime`, `json_query` — seeded with working defaults (no external resources needed).

This is **deliberate**. Every plugin ships inert unless its defaults are genuinely safe. You opt in explicitly.

---

## Further reading

- [tools-and-permissions.md](tools-and-permissions.md) — mental model and the five-layer gate
- [plugin-authoring.md](plugin-authoring.md) — how to write your own plugin
- [security-posture.md](security-posture.md) — threat model
- [wiki/Plugin-Reference-*.md](../wiki/) — the same material split per-plugin in the wiki
