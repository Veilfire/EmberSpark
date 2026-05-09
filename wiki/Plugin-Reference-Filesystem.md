# Plugin Reference: `filesystem`

Bounded read, write, list, and stat against an operator-allowlisted path tree. This is the workhorse for any agent that needs to interact with files.

- **Required permissions:** `fs.read`, `fs.write`, `fs.list`
- **Required secrets:** none
- **Sensitivity:** `MODERATE`
- **Network:** not needed

---

## What the model can do per call

The model calls the plugin with an `op` field:

| `op` | Meaning |
|---|---|
| `read` | Read file contents into a UTF-8 string (or replace-errors fallback) |
| `write` | Create-or-truncate a file with provided content |
| `append` | Append content to an existing file |
| `list` | Directory listing with name, path, size, is_dir |
| `stat` | File metadata |

Arguments:

```json
{
  "op": "read",
  "path": "~/workspace/notes.md",
  "max_read_bytes": 5000000,
  "max_files_per_call": 256
}
```

`allow_paths` and `deny_paths` fields are *operator-overridden* — if you set them in the plugin config, the model cannot widen them. If the model supplies them and you haven't, the model's values are used (and validated against the agent YAML's `permissions.filesystem.allow_paths`).

---

## Configuration fields

### `allow_paths` — list of paths

The directories the plugin is allowed to touch. Paths are expanded (`~` → your home) and resolved via `Path.resolve()` at startup, so symlinks are followed once and the resolved destination is what's checked.

**Working default.** When neither operator config nor per-call args supply `allow_paths`, the plugin falls back to the data volume's `scratch` and `deliverables` roots — both already sandbox-scoped to the agent's grants. That removes the "fresh install hard-fails" footgun while keeping reach inside the data volume. For production, configure explicit operator-side paths.

Typical values:

- `["~/workspace"]` — a dedicated workspace directory
- `["~/Documents/spark-workspace"]` — under Documents for Mac users
- `["/var/lib/myapp/data"]` — a shared data volume

### `deny_paths` — list of paths

Directories the plugin refuses even if they're nested inside `allow_paths`. Applied after the allow check.

Defaults to `["~/.ssh", "~/.aws", "~/.config"]` — blanket protection for common credential locations. You can extend or replace this list.

Example: an agent with `allow_paths: [~/projects]` but `deny_paths: [~/projects/.env-files]` can touch everything under `~/projects` except that one subdirectory.

### `max_read_bytes` — int

Hard ceiling on any single `read` call. Larger files are truncated to this size and the result is tagged `truncated: true`. Default: `5_000_000` (5 MB).

Lower this if you want to prevent the model from pulling large blobs into its context window. Raise it if you need to read larger config files.

### `max_files_per_call` — int

Maximum entries returned by a `list` call. Default: `256`. The plugin stops at this count and sets `truncated: true`.

### `read_only` — bool *(operator-only)*

The master switch. When `true`, **every** write and append call raises `PermissionError` before touching the disk.

Use this when you want an agent to explore a workspace without modifying anything. Common pairings:

- A code-review agent (reads repository, never writes)
- A data-exploration agent (reads files, reports findings)
- A debugging agent (reads logs, suggests fixes)

When you want to let the agent write, flip `read_only: false` and make sure `allow_paths` covers the writable area and `deny_paths` excludes anything you don't want touched.

---

## Safety properties

The filesystem plugin ships with several hardening details you should know about:

### Parent-directory refusal

The plugin **refuses** to write when:

- The parent directory doesn't exist, or
- The parent directory is itself a symlink

This means **you must create parent directories yourself** before the agent runs. The plugin will not `mkdir -p` for you. If the agent needs to write to `~/workspace/reports/2026/Q2/`, you need to `mkdir -p ~/workspace/reports/2026/Q2/` in advance.

This is deliberate. Creating parent directories from inside a plugin opens a TOCTOU window where a malicious process could replace the directory with a symlink between the `mkdir` and the `open`. Refusing the creation closes the window.

### `O_NOFOLLOW` + `O_EXCL` + `O_CLOEXEC`

Every file open uses these flags:

- `O_NOFOLLOW` — refuse to follow symlinks on the final path component
- `O_EXCL` on fresh writes — refuse to open an existing file (retry with `O_TRUNC` only on `FileExistsError`)
- `O_CLOEXEC` — don't leak the file descriptor to child processes

You don't have to do anything with these — they just make the plugin harder to exploit.

### Resolved path comparison

When you set `allow_paths: [~/workspace]`, the plugin resolves `~/workspace` via `Path.resolve()` **at startup**. Afterwards, every call resolves the target path the same way and compares via `Path.relative_to`. Symlinks are followed once and the resolved destination is what's checked — so `~/workspace/symlink-to-etc` doesn't bypass the allowlist because its resolution lands outside `~/workspace`.

---

## Operator workflows

### Workflow 1 — A read-only inspection agent

Agent YAML:

```yaml
spec:
  plugins:
    allow: [filesystem]
  permissions:
    filesystem:
      allow_paths: [~/code/myproject]
    grants:
      - fs.read
      - fs.list
      # note: no fs.write grant — the plugin can never write
```

Plugin config:

```json
{
  "allow_paths": ["~/code/myproject"],
  "deny_paths": ["~/code/myproject/.env", "~/code/myproject/secrets"],
  "max_read_bytes": 500000,
  "max_files_per_call": 128,
  "read_only": true
}
```

Notice the **belt + suspenders**: both the agent YAML and the plugin config deny writes. Either one alone would be sufficient, but having both means a config drift doesn't quietly widen the agent.

### Workflow 2 — A write-enabled report generator

Agent YAML:

```yaml
spec:
  plugins:
    allow: [filesystem, markdown_writer]
  permissions:
    filesystem:
      allow_paths: [~/reports]
    grants:
      - fs.read
      - fs.write
      - fs.list
```

Plugin config for `filesystem`:

```json
{
  "allow_paths": ["~/reports/drafts"],
  "deny_paths": [],
  "max_read_bytes": 2000000,
  "max_files_per_call": 256,
  "read_only": false
}
```

And for `markdown_writer`:

```json
{
  "allow_paths": ["~/reports/final"],
  "deny_paths": [],
  "allow_append": true,
  "allow_overwrite": true
}
```

Note: the filesystem plugin and markdown_writer plugin have **different** `allow_paths`. The filesystem plugin can touch `~/reports/drafts` (where the agent scratches), the markdown_writer can touch `~/reports/final` (where the agent publishes). They're narrowed independently.

### Workflow 3 — A temporary wide grant for a specific task

Sometimes you need the agent to touch a larger area for a one-off run. Rather than widening the permanent config, do it in-UI:

1. Open Plugins → filesystem.
2. Widen `allow_paths` to include the extra directory.
3. Reason: "one-time import from /tmp/dropbox-sync".
4. Save.
5. Run the task.
6. Open Plugins → filesystem again, remove the extra directory.
7. Reason: "revert one-time grant".
8. Save.

The audit log shows both changes, so the paper trail is complete. No YAML edits, no restart.

---

## Common failures and what they mean

### `PathDenied: Path /home/jes/.ssh/id_rsa is outside allow list`

The agent (or the model) tried to touch a path that isn't in `allow_paths`. Check whether:

- You forgot to include the necessary directory in `allow_paths`
- The path goes through a symlink whose resolved destination is outside the allowlist
- The model is genuinely trying to reach something it shouldn't

### `PathDenied: Path /home/jes/workspace/.ssh/id_rsa is inside deny list`

Even though `~/workspace` is in `allow_paths`, the `deny_paths` entry for `~/.ssh` also catches `~/workspace/.ssh`. Adjust `deny_paths` if this is a false positive.

### `PermissionError: filesystem plugin configured read_only`

You set `read_only: true` in the plugin config but the agent tried a `write` or `append`. Either flip the toggle off or narrow the agent's task.

### `PermissionError: refusing write: parent /home/jes/workspace/new-dir must exist and not be a symlink`

The directory doesn't exist yet. Create it with `mkdir -p` before the agent runs.

### `PermissionError: No allow paths configured`

The plugin config is inert. Go to Plugins → filesystem and populate `allow_paths`.

---

## Further reading

- [Using Plugins](Using-Plugins) — the operator workflow overview
- [Plugin Reference: markdown_writer](Plugin-Reference-Markdown-Writer) — the `.md`-specific variant
- [Concepts: The Sandbox](Concepts-Sandbox) — how `rw_paths` bind mounts compose with plugin config
