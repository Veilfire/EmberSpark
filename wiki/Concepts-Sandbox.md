# Concept: The Sandbox

Every tool call in EmberSpark runs inside a fresh child process under a platform-specific OS sandbox. This is not optional.

---

## What the sandbox is

A sandbox is a process-level isolation layer provided by the kernel. EmberSpark uses one of:

- **Bubblewrap** on Linux (default) — unprivileged user namespaces, seccomp filter, RO/RW bind mounts
- **nsjail** on Linux (opt-in) — stricter version with cgroups, rlimits, more aggressive seccomp
- **Seatbelt (`sandbox-exec`)** on macOS (default) — generates an SBPL profile per call

All three give the child process a *narrowed view of the system*. It sees:

- A read-only bind mount of Python and the plugin's module code
- A read-write bind mount of the directories the plugin is allowed to write to (and nothing else)
- Either no network namespace, or the shared one (depending on whether the plugin declared `needs_network`)
- Strict rlimits (CPU seconds, memory, file descriptors, process count)
- A scrubbed environment (no inherited env vars, especially none containing secrets)

The child process **cannot** see:

- Other users' home directories
- `/etc/shadow`, `~/.ssh/`, `~/.aws/`, etc.
- The EmberSpark runtime's own DB or Chroma directory
- Any network interface that isn't in its allowed hosts (when network is denied)
- Any environment variable from the parent process

---

## Why it's mandatory

The Python permission layer ([`ToolExecutor`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/tool_runtime.py)) is already quite strict — it checks allowlists, permissions, budgets, and operator config before dispatching a call. So why the additional sandbox?

Because **the Python layer is one bug away from being bypassable**. A typo in an allowlist, a plugin that doesn't validate its own args, a Pydantic schema with `extra="allow"`, a supply-chain compromise of a dependency — any of these could let a malicious plugin reach beyond its declared scope.

The sandbox catches those bugs at the kernel. If a plugin is somehow able to open `~/.ssh/id_rsa`, the kernel returns `EACCES` because that path isn't in the bind mounts. If the plugin tries to `socket.connect` when `needs_network=False`, the kernel returns `ENETUNREACH` because the network namespace is isolated.

Belt + suspenders.

---

## What each plugin gets

The [`SandboxPolicy`](https://github.com/Veilfire/EmberSpark/blob/main/spark/sandbox/policy.py) is computed fresh for every tool call from the agent's `permissions` block + whether the plugin declared `needs_network`. It contains:

| Field | Derived from |
|---|---|
| `ro_paths` | Python prefix + plugin module directory + operator-declared read-only paths |
| `rw_paths` | `permissions.filesystem.allow_paths` |
| `allow_network` | `plugin.needs_network` AND agent has `net.http` grant. The `allow_hosts` list is **advisory**, not a kill-switch — an empty list no longer disables networking. |
| `allow_hosts` | `permissions.network.allow_hosts` (informational; SSRF defense is per-request inside the plugin via `HostPolicy` + `pin_dns`) |
| `rlimits.cpu_seconds` | `permissions.sandbox.cpu_seconds` |
| `rlimits.memory_mb` | `permissions.sandbox.memory_mb` |
| `rlimits.max_open_files` | `permissions.sandbox.max_open_files` |
| `rlimits.max_processes` | `permissions.sandbox.max_processes` |
| `timeout_seconds` | `permissions.sandbox.timeout_seconds` |
| `env` | Scrubbed: PATH, LC_ALL, PYTHONHASHSEED, HOME=/tmp |

---

## How secrets reach the sandbox

Secrets **never** go through environment variables. EmberSpark's worker process reads secrets via a JSON frame on stdin:

1. Parent process resolves the secrets the plugin needs (`plugin.required_secrets`) from the secret manager.
2. Parent builds a `RequestFrame` with `{plugin_module, plugin_class, args, secrets, plugin_config}`.
3. Parent spawns `python -m spark.sandbox.worker` inside the sandbox.
4. Parent writes the JSON frame to the child's stdin.
5. Child reads the frame, imports the plugin, calls `plugin.execute(args, ctx)` where `ctx.secrets` is the dict from the frame.
6. Child writes a JSON response frame to stdout.
7. Parent reads the response, validates it against the plugin's `output_schema`, filters it, returns to the engine.

The JSON frame on stdin never lives in a file or an env var. `/proc/<child>/environ` is empty of secrets because they aren't in the environment.

---

## Fail-closed startup

`spark serve` runs `check_available()` at startup. If no backend works, it raises `SandboxUnavailable` and exits:

```
sandbox unavailable: No sandbox backend available. Install bubblewrap
(Linux) or ensure sandbox-exec is present (macOS). Windows is not supported.
```

This is **deliberate**. A missing sandbox is a silent downgrade in safety posture, and EmberSpark refuses to do it silently. If the backend goes missing mid-run (unlikely but possible), the next tool call will fail with a classified `sandbox_unavailable` error and the run will fail closed.

---

## What the sandbox does NOT prevent

- **A hostile operator.** If you deliberately want to break out of the sandbox, you can add `/` to `allow_paths` and configure everything wide open. EmberSpark is not trying to defend against the operator.
- **Kernel bugs.** Sandboxes are protected by the same kernel they run on. A kernel vulnerability can allow a sandbox escape. This is a known limitation and the reason firecracker mode exists for the paranoid.
- **CPU side channels.** Co-located processes can still Spectre at you. EmberSpark doesn't claim to defend against that.
- **Resource contention.** If you run 10 agents in parallel, they'll compete for the same CPU and memory. The rlimit is per-sandbox, not per-host.
- **Time-of-check-time-of-use attacks on the host filesystem.** If something rewrites a bind-mounted file while the sandbox is reading it, the sandbox can't prevent that — the file is live. This is why the filesystem plugin uses `O_NOFOLLOW` + `O_EXCL` on the final open.

---

## Backend-specific details

### Bubblewrap (Linux default)

Invocation (simplified):

```
bwrap \
  --die-with-parent \
  --new-session \
  --unshare-user --unshare-uts --unshare-cgroup-try --unshare-ipc \
  --unshare-pid                          # host mode only
  --proc /proc --dev /dev                # host mode only (fresh procfs/devtmpfs)
  --ro-bind /proc /proc                  # containerized mode (nested userns)
  --dev-bind /dev /dev                   # containerized mode
  --ro-bind /usr /usr \
  --ro-bind <python-prefix> <python-prefix> \
  --bind <rw_path_1> <rw_path_1> \
  --bind <rw_path_2> <rw_path_2> \
  --unshare-net                          # only when plugin does NOT need network
  --ro-bind-try /etc /etc                # only when plugin DOES need network
  --setenv PATH /usr/bin:/usr/local/bin \
  --setenv LC_ALL C.UTF-8 \
  --setenv HOME /tmp \
  -- python -m spark.sandbox.worker
```

Bubblewrap on modern kernels uses unprivileged user namespaces — no root required.

**Containerized-mode auto-detect.** When EmberSpark is itself running inside Docker / Podman (`/run/.containerenv` or `/.dockerenv` present), bwrap can't mount fresh `/proc` or `/dev` from inside a nested user namespace — the kernel refuses with `Permission denied`. The backend detects this and switches to bind-mounted `/proc` (read-only) and `/dev` instead. Every other isolation gate (user namespace, UTS, IPC, cgroup, optional net) still applies; only the procfs+devtmpfs operations downgrade to bind-mounts.

**Resolver and CA bundle for networked plugins.** When `allow_network=True`, bwrap also `--ro-bind-try /etc /etc` so glibc's resolver finds `/etc/resolv.conf` and OpenSSL finds the CA bundle. Per-host hostname validation still runs inside the plugin via `HostPolicy.from_list(...)` + `validate_url(...)`, and the plugin's request goes through `pin_dns(target)` so the URL's hostname is preserved for SNI/cert verification while the TCP connection is pinned to the pre-validated IP (defeats DNS rebinding).

### nsjail (Linux opt-in)

Similar invocation but with explicit cgroups and seccomp-bpf filters. Stricter isolation at the cost of slightly higher spawn latency.

### Seatbelt (macOS default)

Renders a [Sandbox Profile Language](https://en.wikipedia.org/wiki/Seatbelt_(software)) file per call and invokes:

```
sandbox-exec -f <profile> python -m spark.sandbox.worker
```

The profile denies everything by default and then allows specific read / write / network subpaths. `sandbox-exec` is deprecated by Apple but still shipped in all macOS releases. If Apple removes it in a future release, EmberSpark will need a new macOS backend.

---

## Tuning rlimits

The defaults are:

```yaml
sandbox:
  cpu_seconds: 30
  memory_mb: 512
  max_open_files: 128
  max_processes: 8
  timeout_seconds: 60
```

These are fine for most workloads — fetching a URL, reading a few files, writing a markdown note. If your plugin needs more:

- **ML inference** — bump `memory_mb` to 2048 or higher; bump `cpu_seconds` to 300 or higher
- **Large SQLite queries** — bump `memory_mb`, keep `cpu_seconds` tight so a runaway query is killed
- **HTTP calls to slow APIs** — bump `timeout_seconds` a little, but not too much (you also want the run to fail fast)

Tune per-agent by editing the agent YAML's `permissions.sandbox` block.

---

## Further reading

- [Concepts: Plugins](Concepts-Plugins) — how plugins are registered and loaded
- [Permissions Guide](Permissions-Guide) — how the sandbox composes with the four Python layers
- [Daemon Modes](Daemon-Modes) — the "naked / Docker / Firecracker" choice for the whole EmberSpark runtime
