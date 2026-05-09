# Plugin Reference: `markdown_writer`

A thin wrapper over `filesystem` that restricts writes to `.md` / `.markdown` files. Use this when you want an agent to produce documentation or notes but not touch other file types.

- **Required permissions:** `fs.write`
- **Required secrets:** none
- **Sensitivity:** `LOW`
- **Network:** not needed

---

## Why have a separate markdown_writer?

You could do everything with `filesystem` + `fs.write`. But a specialized plugin is:

- **Safer** — the extension check is enforced at the Pydantic validator, so the agent can't accidentally write `.env` files, shell scripts, binaries, etc.
- **Clearer** — when you see `markdown_writer` in `plugins.allow`, you know the agent's write scope is "markdown files only."
- **Composable** — you can use the full `filesystem` plugin for reading while restricting writes to this narrower plugin.

Many agents have both `filesystem` (read-only) and `markdown_writer` (write-enabled) in their allowlist. The filesystem plugin has `read_only: true`, the markdown_writer has full write access to a reports directory. Two plugins, two scoped configs.

---

## What the model can do per call

```json
{
  "path": "~/reports/weekly-digest.md",
  "content": "# Weekly Digest\n\n...",
  "mode": "write"
}
```

| `mode` | Meaning |
|---|---|
| `write` | Create-or-truncate the file with the provided content |
| `append` | Append content to an existing file |

The `path` field is validated at the Pydantic layer:

```python
if not v.lower().endswith((".md", ".markdown")):
    raise ValueError("markdown_writer only writes .md / .markdown files")
```

Non-markdown paths are rejected before the plugin even runs.

---

## Configuration fields

### `allow_paths` — list of paths

Directories the plugin may write to. Same semantics as `filesystem.allow_paths` — resolved at startup, enforced per-call via `Path.relative_to`.

**Working default.** When the resolved `allow_paths` is empty, the plugin falls back to `ctx.deliverables_path` (the data volume's deliverables root). That root is already sandbox-scoped to the agent's `fs.write` grant, so the fallback doesn't widen reach — it just removes the "every write fails on a fresh install" footgun. Configure explicit paths for production.

### `deny_paths` — list of paths

Directories that are refused even if nested inside `allow_paths`. Applied after the allow check.

### `allow_append` — bool *(operator-only)*

When `false`, `mode=append` is rejected with `PermissionError`. Forces every write to be a full-file overwrite.

Use this when:

- You want a fresh file every time (reports that shouldn't grow)
- You don't want the agent to "accumulate" content silently

### `allow_overwrite` — bool *(operator-only)*

When `false`, `mode=write` is rejected. Forces every write to be an append — useful for an append-only journal.

Use this when:

- You want the agent to keep a running log where old entries never get rewritten
- You're using the markdown file as an audit trail itself

### Special combinations

| `allow_append` | `allow_overwrite` | Behavior |
|---|---|---|
| true | true | Full access (default) |
| true | false | Append-only — agent can never overwrite an existing file |
| false | true | Overwrite-only — agent always produces a fresh file |
| false | false | **Fails closed** — the plugin can't do anything. Don't do this. |

---

## Safety properties

All the same hardening as `filesystem`:

- **Parent-directory refusal** — the plugin won't `mkdir -p` for you. Create parent directories in advance.
- **`O_NOFOLLOW` + `O_EXCL` + `O_CLOEXEC`** — every open is TOCTOU-hardened.
- **Resolved path comparison** — symlinks are followed during the allow-list check, so you can't escape via a symlink in the workspace.
- **Extension gate** — the `.md` / `.markdown` suffix check happens at the Pydantic validator before anything touches disk.

Plus: markdown_writer's sensitivity is `LOW` (vs. filesystem's `MODERATE`), which means the privacy filter treats its outputs (the `bytes_written` result) as unlikely to contain sensitive data. This matters because tool outputs from markdown_writer are less aggressively filtered before returning to the model — but in practice, the only thing the plugin returns is metadata (path, bytes written, mode), not content.

---

## Operator workflows

### Workflow 1 — Append-only research journal

Agent YAML grants `fs.write` and allows `markdown_writer`. Plugin config:

```json
{
  "allow_paths": ["~/research/journal"],
  "deny_paths": [],
  "allow_append": true,
  "allow_overwrite": false
}
```

The agent can add to `~/research/journal/notes.md` but cannot overwrite it. If the agent wants a "fresh" file, it has to pick a new filename — which is itself constrained to `.md` and to `~/research/journal`.

### Workflow 2 — Daily report generator

```json
{
  "allow_paths": ["~/reports/daily"],
  "allow_append": false,
  "allow_overwrite": true
}
```

Every report is a fresh overwrite. No append-mode confusion, no accumulation. The agent must produce the whole file each time. If the previous run's content is important, it's the agent's responsibility to read it first via `filesystem`.

### Workflow 3 — Read-only exploration + write-enabled output

Two plugins, two configs.

`filesystem` (for reading the source tree):

```json
{
  "allow_paths": ["~/code/myproject"],
  "read_only": true,
  "max_read_bytes": 2000000
}
```

`markdown_writer` (for the output):

```json
{
  "allow_paths": ["~/code/myproject/docs"],
  "allow_append": true,
  "allow_overwrite": true
}
```

The agent can read anything under `~/code/myproject` (including `~/code/myproject/docs`) but can only *write* `.md` files in `~/code/myproject/docs`.

---

## Common failures and what they mean

### `ValueError: markdown_writer only writes .md / .markdown files`

The model tried to write a non-markdown file. The Pydantic validator refused. Either:

- The model's tool call is wrong (ask it to write `.md` instead)
- The task genuinely needs a different extension — use `filesystem` with `fs.write`

### `PermissionError: markdown_writer configured to deny append mode`

You set `allow_append: false` and the model tried `mode=append`. Either flip the toggle or have the model use `mode=write` (but note that this overwrites any existing file).

### `PermissionError: markdown_writer configured to deny overwrite mode`

You set `allow_overwrite: false` and the model tried `mode=write`. Either flip the toggle or have the model append to an existing file.

### `PathDenied: Path /home/jes/.ssh/notes.md is outside allow list`

The `.md` check passed (because the path ended in `.md`) but the path is not in `allow_paths`. Even a `.md` file outside your workspace is refused.

### `PermissionError: refusing write: parent /home/jes/reports/2026/Q2 must exist and not be a symlink`

Same as the filesystem plugin — you need to create parent directories in advance.

---

## Further reading

- [Plugin Reference: filesystem](Plugin-Reference-Filesystem) — the unrestricted parent plugin
- [Using Plugins](Using-Plugins) — the operator workflow overview
- [Concepts: Plugins](Concepts-Plugins) — how plugins are configured and invoked
