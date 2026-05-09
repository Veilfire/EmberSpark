# EmberSpark Security Posture

This is the canonical security reference for EmberSpark. It covers the threat model, the OWASP mappings, the sandbox design, the current set of hardening measures (including fixes from the cross-cutting review), and the known v1 tradeoffs. This document is treated as part of the codebase — keep it honest, keep it current.

---

## Threat model

EmberSpark's threat model assumes:

- **The model may be hostile** — prompt injection, goal drift, tool misuse, exfiltration attempts, attempts to widen its own reach.
- **Plugins may be buggy or compromised** — a malicious dependency, a typo that turns an allowlist off, a symlink swap race.
- **Tool outputs may be attacker-controlled** — arbitrary web responses, arbitrary file contents, arbitrary JSON from third-party APIs.
- **Secrets must never reach** the model, memory, or logs by default.
- **The host is trusted** — EmberSpark is a single-user local-first runtime. We do not defend against a hostile root or a compromised kernel.
- **The operator is trusted but fallible** — they might misconfigure something, paste a token in the wrong place, or forget to rotate credentials.

### Out of scope

- Multi-tenancy.
- Remote policy enforcement.
- Transport-layer attestation of the model provider.
- Windows (the mandatory OS sandbox needs Bubblewrap or Seatbelt).
- Defending against the operator actively trying to exfiltrate their own data.

---

## Defense in depth

Every side effect passes through **two** enforcement layers:

1. **Python policy layer** — [`spark/plugins/tool_runtime.py`](../spark/plugins/tool_runtime.py):
   plugin allowlist → granted permissions → budget → operator plugin-config merge → Pydantic schema → secret scoping → privacy filter.
2. **OS sandbox layer** — [`spark/sandbox/executor.py`](../spark/sandbox/executor.py):
   Bubblewrap / Seatbelt / nsjail wraps the tool child process with scoped bind mounts, seccomp, rlimits, namespace unshare, and network denial by default.

A compromised plugin that bypasses the Python layer is still unable to write outside its bound mounts or reach blocked IPs. **The sandbox layer cannot be disabled** — `spark serve` refuses to start without a working backend.

---

## OWASP LLM Top 10 (2025) mapping

Each row is a concrete control, not a promise.

| Risk | EmberSpark control | Enforced in |
|---|---|---|
| **LLM01 Prompt injection** | Tool outputs are filtered and tagged before model exposure. Retrieved memories are re-filtered through `filter_for_model` before prompt assembly (fix from security review). Retrieval is capped by `top_k`, `min_score`, and sensitivity ceiling. | [`privacy/filtering.py`](../spark/privacy/filtering.py), [`runtime/engine.py`](../spark/runtime/engine.py) |
| **LLM02 Sensitive info disclosure** | Secrets are `SecretStr` end-to-end. Injected only into `ToolContext.secrets` for declared `required_secrets`. Redaction pipeline strips API keys / JWTs / PEM / cloud-metadata URLs / high-entropy tokens (16+ chars). Raw prompt + model output logging is off by default and gated by an elevated audit event. | [`secrets/`](../spark/secrets/), [`privacy/redaction.py`](../spark/privacy/redaction.py), [`logging/processors.py`](../spark/logging/processors.py) |
| **LLM03 Supply chain** | `uv.lock` committed. `pip-audit` + `bandit` in CI. Plugins must opt into load via operator config; discovery ≠ enable. Plugin module hash recorded in `plugin_registry` on first load; drift emits an event. | [`plugins/registry.py`](../spark/plugins/registry.py), CI |
| **LLM04 Data & model poisoning** | Memory promotion is gated through reflection → classification → redaction → dedup → embedding → write. Reviewer-edited skill descriptions are re-filtered before they reach the prompt. No raw-transcript dumps. | [`memory/promotion.py`](../spark/memory/promotion.py), [`reflection/`](../spark/reflection/) |
| **LLM05 Improper output handling** | All tool inputs validated against Pydantic schemas. `ValidationError` details are logged to operator logs but **never** echoed back to the model (fix from security review — schema field names can't leak). Every tool process runs in the OS sandbox with scoped bind mounts. File paths go through `Path.resolve()` + `is_relative_to()` + `O_NOFOLLOW` + refused-symlink-parent. | [`plugins/tool_runtime.py`](../spark/plugins/tool_runtime.py), [`sandbox/`](../spark/sandbox/), [`plugins/builtins/filesystem.py`](../spark/plugins/builtins/filesystem.py) |
| **LLM06 Excessive agency** | Per-run budgets (iterations, model calls, tool calls, wall time). Per-agent plugin allowlist. Per-tool permission schema. **Operator plugin config overrides model args on overlapping fields** — the model cannot widen an operator-narrowed `allow_hosts` or `allow_paths`. Sandbox enforces scoped FS and net at kernel level. | [`runtime/engine.py`](../spark/runtime/engine.py), [`plugins/config.py`](../spark/plugins/config.py), [`sandbox/`](../spark/sandbox/) |
| **LLM07 System prompt leakage** | System prompts composed from the active persona + retrieved memory in a clearly-bounded section. Raw prompt and raw output logging are off by default; enabling either requires an explicit edit with an elevated audit entry. | [`runtime/engine.py`](../spark/runtime/engine.py), [`logging/`](../spark/logging/) |
| **LLM08 Vector/embedding weaknesses** | Per-namespace Chroma collections. Sensitivity metadata enforced at query time (`strict` mode refuses to retrieve `high`/`restricted`). No cross-namespace retrieval. | [`memory/long_term.py`](../spark/memory/long_term.py) |
| **LLM09 Misinformation** | Reflection writes `confidence` + `source_type`. Retrieval rerank combines similarity with confidence and recency. Memory records are distilled summaries, never quotes. | [`memory/retrieval.py`](../spark/memory/retrieval.py), [`reflection/`](../spark/reflection/) |
| **LLM10 Unbounded consumption** | Layered budgets (wall clock via `asyncio.wait_for`, `recursion_limit` on LangGraph, `BudgetGuard` iter/model/tool counters). Default timeouts on every HTTP/tool call. Max response size on `http_client`. Cost tracker enforces per-agent/provider hard-stop budgets before every run. | [`runtime/engine.py`](../spark/runtime/engine.py), [`plugins/builtins/http_client.py`](../spark/plugins/builtins/http_client.py), [`cost/tracker.py`](../spark/cost/tracker.py) |

### OWASP Agentic AI Top 10 (Dec 2025) additions

- **Tool misuse / unauthorized action** — every plugin declares `required_permissions`. Missing a permission → deny (no "default allow"). Missing from the agent allowlist → deny.
- **Memory corruption** — long-term writes are idempotent by `memory_id`, hash-checked against existing canonical text, dedup'd pre-write.
- **Resource exhaustion** — budgets + APScheduler `max_instances=1` + exponential backoff on scheduler failures + heartbeat-based liveness for perpetual tasks.
- **Goal misalignment** — reflection summary must include an explicit `success` flag. Failed runs do not promote memories by default. Playbook bandit only reinforces on success.
- **Skill discovery drift** — new skills require human review. Skill discovery is constrained to a **separate** trusted-docs allowlist, distinct from the agent's general network grants.

### OWASP Web/API Top 10 (relevant)

- **A01 Broken access control** — plugin allowlist per agent; per-tool permission schema; typed-confirmation gates on elevated toggles (internal IPs, raw logging).
- **A03 Injection** — No shell in the default built-ins is raw-string; `shell` plugin uses argv-only with operator-allowlisted commands and explicit flag allowlists. SQL via SQLModel (parameterized). `sqlite` plugin pre-parses SQL with `sqlglot` and gates statement types. YAML loads via `ruamel.yaml.YAML(typ='safe')` only.
- **A05 Security misconfiguration** — strict privacy mode is default. Raw logging requires explicit YAML. Web UI is **disabled by default**. Shell plugin `enabled=False` by default.
- **A08 Software & data integrity** — Locked deps. Plugin contracts carry version + hash. **Log hash-chain** (`file.header` carries `prev_sha256`) so rotated files can be verified with `spark logs verify`.
- **A09 Security logging** — JSONL events include event_type, run_id correlation, span_id, redaction_applied flag, budget_state. Critical mutations go through the audit log at `elevated` / `critical` severity.
- **A10 SSRF** — IDN normalization + punycode fail-closed, IPv4/v6 blocklist including IPv4-mapped IPv6 unwrap, cloud metadata exact-match, IP-pinning httpx transport.

---

## SSRF defense ([`spark/utils/net.py`](../spark/utils/net.py))

Every outbound HTTPS request that originates from the `http_client` plugin runs through `validate_url(url, policy)`:

1. **Scheme check** — must be `https` (or explicit `allow_http=True` in plugin config).
2. **Hostname allowlist** — normalized to lowercase, IDN-encoded to punycode, post-encoding `isascii()` assertion (fix from security review — IDNA silent fallback previously allowed homoglyphs).
3. **DNS resolution** — `socket.getaddrinfo` returns every candidate IP.
4. **IP validation** — each candidate is checked against:
   - loopback (`127.0.0.0/8`, `::1`)
   - private (RFC1918, ULA)
   - link-local (`169.254/16`, `fe80::/10`)
   - multicast, reserved, unspecified
   - cloud metadata exact matches (`169.254.169.254`, `fd00:ec2::254`, Alibaba `100.100.100.200`, etc.)
   - IPv4-mapped IPv6 — unwrapped and re-checked (so `::ffff:127.0.0.1` is blocked)
5. **IP pinning** — the outbound request is made against the resolved IP with the original `Host` header. **No second DNS lookup at connect time**, which defeats DNS rebinding.
6. **No redirects** by default; if enabled per-call, the target URL is re-validated through the same pipeline.
7. **Max response size** with streaming byte counter.
8. **Per-request timeouts** (default: 5s connect / 15s read / 5s pool).

---

## Path traversal defense ([`spark/utils/paths.py`](../spark/utils/paths.py))

- `PathPolicy.check(target)` resolves the target via `Path.resolve()` (follows symlinks) before comparing with the allow / deny bases.
- Allow list is enforced first, then deny list.
- Empty allow list is **fail closed** — no access at all.
- The filesystem and markdown_writer plugins (fix from security review):
  - refuse to write when the parent directory doesn't exist or is a symlink
  - use `O_NOFOLLOW | O_CLOEXEC` on every open
  - use `O_CREAT | O_EXCL` for fresh writes, retrying with `O_TRUNC` on `FileExistsError`
- No plugin creates parent directories — the operator is responsible for laying out the workspace in advance.

---

## Plugin sandbox ([`spark/sandbox/`](../spark/sandbox/))

Every plugin execution runs in a fresh child process under a platform-specific sandbox backend. The Python `ToolExecutor` checks still happen (belt), but the final execution is also confined by the kernel (suspenders).

**Backends:**

| Backend | Platform | Default? | Notes |
|---|---|---|---|
| **Bubblewrap** | Linux | yes | Unprivileged user namespaces, seccomp, RO bind mounts, `--unshare-net` when not needing network |
| **Seatbelt (`sandbox-exec`)** | macOS | yes | Generates an SBPL profile per call; `sandbox-exec` is deprecated by Apple but still shipped |
| **nsjail** | Linux | opt-in | Stricter isolation via cgroups + seccomp-bpf + rlimits; chosen via `sandbox.backend: nsjail` in agent YAML |

**Sandbox policy** ([`spark/sandbox/policy.py`](../spark/sandbox/policy.py)) is derived from the agent's `permissions` block + whether the plugin needs network. The policy includes scoped RO / RW bind mounts, rlimits (CPU seconds, memory, max open files, max processes), network allow/deny, and wall clock timeout. The plugin cannot modify this policy.

**IPC** ([`spark/sandbox/ipc.py`](../spark/sandbox/ipc.py)): single JSON frame on stdin, single JSON frame on stdout. Secrets travel in the stdin frame as raw strings — never as env vars, because `/proc/<pid>/environ` leaks env.

**Fallback policy**: if no sandbox backend is available, EmberSpark refuses to start with a clear diagnostic. There is no "unsandboxed" code path.

---

## Secrets ([`spark/secrets/`](../spark/secrets/))

- Values are `SecretStr` end-to-end. `SecretManager.get(name)` returns `SecretStr`; there is no "give me the plain string" API.
- Injection is scoped: `ToolContext.secrets` only contains keys the plugin declared in `required_secrets`. A plugin that forgot to declare a secret can never see it.
- Tracked secret values are scrubbed from every log leaf by the `make_scrub_processor` structlog processor — it walks dicts, lists, and strings and replaces any known secret value with `***`.
- `.env` backend emits a loud warning at first access. Keyring is preferred.
- Web credentials (the UI username/password) live in `~/.spark/web-credentials.json` as a bcrypt hash (13 rounds — bumped from the initial 12 per security review). The cleartext is displayed **once** on startup and discarded.

---

## Plugin configuration ([`spark/plugins/config.py`](../spark/plugins/config.py))

Each built-in plugin declares a Pydantic `config_schema`. Operator-edited values live in the `plugin_configs` SQLite table and are served by `GET /api/plugin-config/{name}`. At tool-call time, [`ToolExecutor.call`](../spark/plugins/tool_runtime.py) loads the row and passes it through `merge_config_and_args`:

- **Operator config wins on overlapping input_schema fields.** If the model supplies `allow_hosts: ["evil.com"]` but the operator has narrowed to `["api.github.com"]`, the merge returns the operator's value.
- **Operator-only fields** (like `read_only`, `allowed_methods`, `allow_append`) are passed to the plugin via `ctx.plugin_config`, not through the model-visible `input_schema`.
- Every mutation to a plugin config is audited at `elevated` severity.

See [plugin-config.md](plugin-config.md) for the full operator-facing reference.

---

## Persona hot reload

The engine's `_system_prompt` is async and re-reads the active persona from the DB on **every** call to `_invoke_model`. Persona edits in the UI take effect on the next iteration of the current run — there is no restart and no cache to invalidate. Sub-ms overhead.

Personas must be deleted-while-inactive — the repository refuses to delete the active persona.

---

## Privacy modes and redaction

Three modes:

- **strict** (default) — full redactor chain (regex + entropy + Presidio), strict sensitivity gates (`high`/`restricted` blocked from model exposure), raw prompt/output logs off.
- **balanced** — same redactor chain, looser sensitivity gate (`high` allowed in memory), raw logs off unless explicitly enabled.
- **regex_only** — skip Presidio (saves ~500MB install). Regex + entropy still run.

The redaction pipeline:

1. **detect-secrets-style regex** — AWS, GCP, Stripe, OpenAI, OpenRouter, Anthropic, GitHub, Slack, JWT, PEM blocks, cloud metadata URLs
2. **Entropy detector** — Shannon entropy ≥ 4.0 bits/char on strings 16+ chars long (lowered from 24 per security review)
3. **Presidio NER** — names, emails, credit cards, SSN, IBAN, phones, addresses, IPs (lazy-loaded; prewarm via `spark doctor check`)
4. **Structural filtering** — field drops, size caps, summary substitution

Every log leaf is walked by the structlog scrub processor before being serialized, and `redaction_applied` is added to the event dict whenever the walker changes anything.

---

## Web UI security

- **Fail-closed default** — `spec.web.enabled: false` in `~/.spark/spark.yaml` is the default. `spark serve` refuses to start without an explicit opt-in.
- **Three bind modes** — `loopback` (default), `lan` (requires `allowed_cidrs`), `public` (requires TLS cert + key).
- **CIDR allowlist middleware** — non-loopback binds enforce a source-IP allowlist. `X-Forwarded-For` is only honored from `trusted_proxies`, and the leftmost entry is parsed as a real IP before being trusted (fix from security review).
- **Credentials** — username (`dictionary-word<4 digits>`) and 16-char password (two words + digits + special + exactly one uppercase). bcrypt-hashed (13 rounds). Displayed once on stderr. Rotated on every `spark serve` by default (`rotate_on_startup: true`).
- **Session cookie** — HttpOnly, SameSite=Strict, `Secure` auto-enabled for public bind or `SPARK_WEB_COOKIE_SECURE=1`.
- **WebSocket auth** — session cookie via `spark_session`, or `?token=…` query param compared with `secrets.compare_digest` (fix from security review — previously a plain `!=`).
- **Session validation** — chat WebSocket verifies the `session_id` against the DB **before** entering the receive loop so an unauthenticated scan can't probe arbitrary session IDs.
- **CSRF** — `SameSite=Strict` cookies + `Content-Type`-only CORS headers. CORS wildcard (`*`) is refused at startup if `SPARK_WEB_ALLOW_ORIGIN=*` is set with credentials.
- **Security headers** — every response carries CSP, X-Frame-Options: DENY, X-Content-Type-Options, Referrer-Policy, COOP, COEP, COR-Policy, Permissions-Policy. HSTS is set **only** when the request is HTTPS (so localhost HTTP doesn't get stuck in an HSTS cache).
- **Rate limit** — per-IP sliding window, configurable in `web.rate_limit_per_minute`.
- **Structured auditing** — every mutation to security config writes an `audit_log` entry. Critical mutations (freeze, internal-IP grants, raw logging, trusted-doc edits) use `critical` severity.
- **Typed confirmation** — elevated toggles require the operator to type a specific string (the agent name for internal-IP grants, the literal `confirm` for raw logging + freeze).
- **Unvalidated body rejection** — every endpoint uses strict Pydantic models with `extra="forbid"` and length caps (fix from security review — previous `dict[str, str]` bodies removed).
- **Validation-error redaction** — Pydantic `ValidationError` details are logged for operators but never echoed back in HTTP responses or to the model, so schema field names can't leak.

---

## Audit log

Every security-relevant mutation writes to `audit_log` with a structured `diff` and `severity`. Critical entries surface in the UI Incident Banner immediately. You can query them via `GET /api/audit/` with `kind` and `min_severity` filters, or from the CLI:

```bash
spark logs tail     # live JSONL stream
spark logs verify   # walk rotated files and verify the hash chain
```

The **hash chain** (`file.header` event with `prev_sha256`) makes the log retroactively tamper-evident: if an attacker deletes a rotated log file, the next file's header will fail the chain check.

---

## v1 tradeoffs (documented)

1. **`sandbox-exec` is deprecated on macOS.** It still ships and is still the only unprivileged sandbox. If Apple removes it, EmberSpark will need a new macOS backend.
2. **Network namespace inside Bubblewrap.** Full per-host veth routing is out of scope. The HTTP client plugin performs SSRF defense at the Python layer and runs inside bwrap with filesystem isolation. When `net=deny`, `--unshare-net` is passed and the child has no network at all.
3. **Presidio false negatives.** NER is best-effort. The redactor runs Presidio *after* deterministic regex, so known-shape secrets are caught even if Presidio misses them.
4. **Plugin hash drift** is logged but not refused — that would break `pip install -U`. A stricter mode (`plugins.require_pinned_hash`) is planned for a later release.
5. **Single-agent runtime.** EmberSpark is designed for one agent per `spark serve` instance. Multi-agent orchestration and sub-agents are explicitly future work.

---

## Responsible disclosure

Security issues: please email `security@spark.dev` (placeholder — replace with the real address at ship time). Do not file public GitHub issues for potential vulnerabilities.
