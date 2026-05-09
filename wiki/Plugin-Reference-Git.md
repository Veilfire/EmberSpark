# Plugin Reference: `git`

Narrow git operations on operator-allowlisted repos. The plugin wraps the `git` binary with argv-only dispatch (same pattern as `shell`) and returns structured output per op — parsed commits for `log`, parsed status entries for `status`, raw text for `diff`/`show`.

- **Required permissions:** `fs.read`, `subprocess` (and `fs.write` when `allow_write=true`)
- **Required secrets:** none
- **Sensitivity:** `MODERATE`
- **Network:** not needed
- **Dependencies:** a `git` binary on PATH inside the sandbox

---

## Why a dedicated git plugin instead of `shell`

You could allowlist `git log` / `git status` as shell commands — but the agent would get back raw text it has to parse. This plugin returns:

- **`log`** → list of `GitCommit(sha, author, date, subject)` objects
- **`status`** → list of `GitStatusEntry(path, index, worktree)` objects
- **`branch`** → list of branch names + the current branch

The model saves tokens and avoids brittle string parsing.

---

## Configuration fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `allow_repos` | list of paths | `[]` | Repo roots (or ancestors) the plugin may operate on. |
| `allow_write` | bool | `false` | When `false`, only `status`/`log`/`diff`/`show`/`branch` are allowed. |
| `max_log_entries` | int | `500` | Per-call cap on `log` results. |
| `max_diff_bytes` | int | `1_000_000` | Per-call cap on `diff`/`show` output. |
| `max_status_entries` | int | `500` | Per-call cap on `status` entries. |
| `git_binary` | string | `git` | Defaults to the binary on PATH. |
| `timeout_seconds` | int | `30` | Per-op wall-clock timeout. |

---

## Supported ops

| Op | Read-only | Purpose |
|---|---|---|
| `status` | ✓ | `git status --porcelain=v1 -z` → list of entries |
| `log` | ✓ | `git log` with stable separators → list of commits |
| `diff` | ✓ | `git diff` text (with `ref` + `path` filters) |
| `show` | ✓ | `git show <ref>` |
| `branch` | ✓ | `git branch --list` → list + current |
| `add` | ✗ | `git add -- <path>` |
| `commit` | ✗ | `git commit -m <message>` |

---

## What the model sends per call

```json
{
  "op": "log",
  "repo": "~/projects/myrepo",
  "limit": 20,
  "since": "1 week ago",
  "ref": "main"
}
```

Returns:

```json
{
  "op": "log",
  "repo": "/home/me/projects/myrepo",
  "commits": [
    {"sha": "abc1234...", "author": "Jane <jane@example.com>", "date": "2026-04-13T15:22:01+0000", "subject": "Fix flaky test"}
  ]
}
```

---

## Operator workflow

**Start read-only.** Set `allow_write: false`. Most agent use cases only need `status` / `log` / `diff` / `show`.

**Narrow `allow_repos` to the specific projects the agent needs.** Don't grant `~/` or `~/projects` as a whole.

**Safe-ref check.** The plugin's ref validator rejects anything outside `^[A-Za-z0-9._/\-]+$`. This prevents tag-injection-style attacks where a malicious ref name causes git to interpret it as a flag.

**Pair with `shell` when you need more.** If you want `git pull` or `git push`, either widen this plugin (risky — it'd need network + credentials handling) or use the `shell` plugin with those commands allowlisted (also risky but in a different way).

---

## Common pitfalls

- **`.git` missing** — the plugin verifies `repo / ".git"` exists before running any op. If you pointed at the wrong directory you'll get a clear error.
- **Ref with `..`** — rejected by the safe-ref regex, which intentionally does not include `.` adjacent to itself. Use absolute SHAs or branch names.
- **`git commit` fails with `nothing to commit`** — the plugin surfaces this as `PermissionError` with git's stderr. It's informational, not a bug.

---

## Further reading

- [Plugin Reference: shell](Plugin-Reference-Shell) — for ops outside this plugin's whitelist
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md)
