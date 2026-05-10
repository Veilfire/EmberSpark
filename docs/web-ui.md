# EmberSpark Web UI

A privileged operator console for EmberSpark, backed by FastAPI and served by a React + TypeScript + Tailwind frontend. This is the main interaction surface for day-to-day use.

This page is the **reference**. For a step-by-step guide, see [wiki/Web-UI-Guide.md](../wiki/Web-UI-Guide.md). For security details, see [security-posture.md](security-posture.md).

## Running

### Loopback (recommended for a laptop)

```bash
spark config init
$EDITOR ~/.spark/spark.yaml
```

Set:

```yaml
spec:
  web:
    enabled: true
    bind:
      mode: loopback
      host: 127.0.0.1
      port: 7777
    credentials:
      rotate_on_startup: true
```

Then:

```bash
spark serve
```

Credentials are printed once to stderr. Save them.

### LAN bind (for other devices on your network)

```yaml
spec:
  web:
    enabled: true
    bind:
      mode: lan
      host: 0.0.0.0
      port: 7777
      allowed_cidrs:
        - 192.168.1.0/24
      trusted_proxies: []
```

`allowed_cidrs` is a required RFC1918 allowlist. Source IPs outside it get a 403 from the CIDR middleware.

### Public bind (requires TLS)

```yaml
spec:
  web:
    enabled: true
    bind:
      mode: public
      host: 203.0.113.10
      port: 443
      allowed_cidrs:
        - 203.0.113.0/24
      tls:
        cert_file: ~/certs/spark.crt
        key_file: ~/certs/spark.key
```

TLS is **mandatory**. The schema validator refuses to load a public bind without it.

### Frontend dev mode

```bash
cd spark/web/frontend
npm install
npm run dev     # Vite on :5173, proxies /api to :7777
```

Production build:

```bash
npm run build   # outputs to spark/web/static
```

`spark serve` automatically serves the built SPA if present.

---

## Auth model

Three identities:

- **Operator account** — username + password (minted on startup, displayed once, bcrypt-hashed at 13 rounds). This is the primary login.
- **Headless token** — `~/.spark/web-token` (mode 0600). For CI, scripts, API clients. Pass as `X-Spark-Token` header. Token-holder gets admin role.
- **WebSocket auth** — session cookie via same-origin upgrade, or `?token=…` query param using `secrets.compare_digest`.

### Roles

| Role | Can do |
|---|---|
| **viewer** | Read every endpoint. Cannot mutate. |
| **operator** | Create / update agents, tasks, personas, plugin configs, memories, budgets, skills. Cannot flip global posture flags. |
| **admin** | Everything, including freeze, raw logging, internal-IP grants, trusted-doc edits, webhook trigger creation. |

The default username/password login lands on **operator**. The token header short-circuits to **admin** for power users.

---

## Pages

The sidebar groups pages into four sections — **RUN** / **OBSERVE** / **SECURE** / **SYSTEM**. Order in the table below mirrors `Shell.tsx` exactly.

### RUN

| Page | Route | What it shows | Min role |
|---|---|---|---|
| **Overview** | `/` | Spend (24h), active runs, posture, recent runs, current privacy default | viewer |
| **Agents** | `/agents` | List of installed agent YAMLs with status, runtime budget, persona, plugin allowlist; click into per-agent detail at `/agents/:name`. | viewer |
| **Chat** | `/chat` | Session-based conversational UI. WebSocket streaming. Failed turns expose a **Why?** toggle that opens the [Failure Inspector](Failure-Inspector-Guide). Mobile-responsive. | viewer (operator to send) |
| **Runs** | `/runs` | Filterable run list with outcome, budgets, error. Click a run to see its replay. | viewer |
| **Run Replay** | `/runs/:run_id/replay` | Per-run outcome + flame graph + iteration timeline. Failures with a `SparkError` payload render an inline FailureInspector. Driven by `task_runs.result_text`, `task_runs.summary`, `task_runs.trigger_payload_json`, `task_runs.error` (now JSON when structured), `deliverables`, and `run_spans` tables. | viewer |
| **Scheduler** | `/scheduler` | Agents, tasks, schedules, **triggers**. Per task: edit / trigger now / pause / stop. Per trigger: auth mode chip, fire counters, lock state, delete. **+ New task** opens the task creator (mode + visual cron builder + budget overrides). **+ New trigger** opens the webhook creator (auth mode + payload forwarding + event filter). | operator (admin to create / delete triggers) |
| **Templates** | `/templates` | Library of starter agent / task templates the operator can fork into a new YAML. | operator |

### OBSERVE

| Page | Route | What it shows | Min role |
|---|---|---|---|
| **Cost** | `/cost` | Period breakdowns (day / week / month), per-provider / per-agent / per-model. Create budgets with soft alerts and hard stops. | operator |
| **Memory** | `/memory` | Long-term memory index browser + playbook stats (success rate, uses, avg tool calls). | operator |
| **Skills** | `/skills` | Pending skill review queue (editable name/description/notes), approved skill list, disable control. | operator |
| **Stats** | `/stats` | Rolling 7-day agent metrics — success rate, p50/p95 wall time, total cost, memory writes, skill approvals. | viewer |
| **Downloads** | `/downloads` | Files plugins wrote into the deliverables directory, cross-linked back to the run that produced them. | viewer |

### SECURE

| Page | Route | What it shows | Min role |
|---|---|---|---|
| **Security Center** | `/security` | Multi-tab policy editor (see below). | operator / admin |
| **Secrets** | `/secrets` | Name-only secret list + canary test (verify a secret is reachable without returning the value). Canary tests audited at `info`. | admin |
| **Guardrails** | `/guardrails` | Last 24h aggregation of critical/elevated/info events. Each category links to `/audit?kind=…`; chevron expands a Top-N offenders mini-table. | viewer |
| **Filtering** | `/filtering` | Operator surface for the data-class guardrail engine. Per-category level / scopes / mask style / min-confidence / consensus + per-detector enable/disable in an Advanced drawer + dry-run sandbox + **Grants drawer** (time-bounded data-class carve-outs with typed-confirm). Mutations audit at `elevated` under `kind=security.filtering.*`; grants at `critical` under `security.data_class.grant`. | admin |
| **Forensic** | `/forensic` | Encrypted per-run capture browser (prompts, model outputs, tool calls, memory events) gated behind opt-in + per-run age identity in the secrets vault. | admin |
| **Audit Log** | `/audit` | Immutable change history with `kind` (also reads `?kind=…` URL param) and `min_severity` filters. Chevron-expand row → inline FailureInspector when the diff carries a SparkError shape. | viewer |

### SYSTEM

| Page | Route | What it shows | Min role |
|---|---|---|---|
| **Provider** | `/provider` | LLM provider selection, API key entry, model probe + connectivity check. Source for everything the runtime sends to a model. | admin |
| **Persona** | `/persona` | List / create / edit / activate personas. Includes a preview button that renders the assembled system prompt. Activate changes land on the very next model call (hot reload). | operator |
| **Plugins** | `/plugins` | List of registered plugins with dynamic form editors for each one's `config_schema`. This is the only place you need to edit plugin behavior. | operator |
| **Ops** | `/ops` | Sandbox backend health, data residency (DB/Chroma/logs disk footprint), plugin registry + hashes, live JSONL log tail (50-line backfill on connect). | viewer |
| **Settings** | `/settings` | Notification category toggles (per-kind, including the five new `gate_*` families), session timeout, sound on / off. | admin (notification toggles), admin (session timeout) |

---

## Security Center tabs

All mutations audit-logged.

| Tab | What it controls |
|---|---|
| **Global Posture** | Emergency freeze, compliance mode (standard/audit), default privacy mode, elevated master toggles (internal-IP access, raw logging). Elevated toggles require typed `confirm`. |
| **Network** | Per-agent outbound policy: allow_hosts, allow_http, timeouts, max response bytes. **Internal-IP grants** with CIDR, reason, TTL (max 24h), typed-agent-name confirmation. |
| **Filesystem** | Per-agent `allow_paths`, `deny_paths`, max read bytes, max files per call. |
| **Sandbox** | Per-agent backend selection (`auto`/`bubblewrap`/`nsjail`/`seatbelt`), rlimits (CPU seconds, memory MB, max open files, max processes, wall timeout). **Cannot be disabled** — shown read-only with a "mandatory" badge. Includes a one-click self-test runner. |
| **Plugins** | Per-agent plugin allowlist + permission grant matrix (fs.read, fs.write, net.http, secrets.read, subprocess). |
| **Privacy** | Per-agent privacy mode, raw prompt / raw output logging toggles (both require double confirmation because they bypass redaction defaults). |
| **Secrets** | Name-only secret list + canary test (verify a secret is reachable without returning the value). Canary tests are audited at `info` so enumeration attempts are visible. |
| **Trusted Docs** | The skill-discovery allowlist (distinct from the agent's network allowlist). Operators can add or remove doc hosts; default list is built in. |

---

## Persona hot reload flow

The persona edit loop is designed so you can iterate live without killing a running task.

1. Go to **Persona**.
2. Pick an existing persona or click **New persona**.
3. Edit `system_prompt`, `tone`, `description`, `tags`. Click **Preview** to see exactly what the model will receive.
4. Click **Save & Activate**.
5. The next model call inside any running task picks up the new persona. Currently in the middle of a chat? The next user turn uses the new persona. Running a one-shot task? The next LangGraph iteration uses the new persona.

Under the hood: `RuntimeEngine._system_prompt` is async and calls `PersonaRepository.get_active()` on every iteration. Sub-ms cost; no cache.

See [persona-manager.md](persona-manager.md) for the conceptual deep dive.

---

## Plugin config flow

All five built-in plugins are operator-configurable via the Plugins page.

1. Go to **Plugins**.
2. Pick a plugin from the left sidebar.
3. Each field in the plugin's `config_schema` is rendered as a form input (strings, numbers, booleans, lists, enums).
4. Type a **reason** (required, audited).
5. Click **Save**.

Operator values **override** the model's per-call args on overlapping fields. If you narrow `http_client.allow_hosts` to `["api.github.com"]`, the model cannot widen it back — attempts are refused at the merge step, before they ever leave the parent process.

Operator-only fields (fields in `config_schema` that aren't in the plugin's `input_schema`) reach the plugin via `ctx.plugin_config` for in-plugin enforcement.

See [plugin-config.md](plugin-config.md) for a per-plugin reference of every configurable field.

---

## Command palette

Open with `cmd+K` (macOS) or `ctrl+K` (Linux). Fuzzy search across every page. `esc` to close.

The `g`+letter chord shortcuts that earlier releases shipped were removed because they fired while typing in chat / settings inputs, teleporting the operator mid-sentence. The palette covers every navigation target without that hazard.

---

## Incident banner

When a `critical`-severity audit entry lands (freeze, internal-IP grant, raw logging toggle, plugin hash drift, webhook rate limit trip), the UI surfaces a red banner across the top of the page until you dismiss it. The banner polls `/api/audit/?min_severity=critical` every 30 seconds.

---

## API shape

JSON REST under `/api/*`. OpenAPI schema at `/api/docs`.

Full route list (abbreviated):

```
GET    /api/health
POST   /api/auth/login
POST   /api/auth/logout
GET    /api/auth/me

GET    /api/scheduler/agents
GET    /api/scheduler/tasks
GET    /api/scheduler/runs
GET    /api/scheduler/schedules
POST   /api/scheduler/trigger
POST   /api/scheduler/simulate
GET    /api/scheduler/approvals
POST   /api/scheduler/approvals/{task_name}
POST   /api/scheduler/dlq/{task_name}/ack
GET    /api/scheduler/triggers
POST   /api/scheduler/triggers
DELETE /api/scheduler/triggers/{trigger_id}
POST   /api/scheduler/webhooks/{trigger_id}

WS     /api/chat/ws/{session_id}
GET    /api/chat/sessions
POST   /api/chat/sessions
GET    /api/chat/sessions/{id}/history

GET    /api/persona/
GET    /api/persona/active
GET    /api/persona/{id}
POST   /api/persona/
PUT    /api/persona/{id}
POST   /api/persona/{id}/activate
POST   /api/persona/{id}/preview
DELETE /api/persona/{id}

GET    /api/plugin-config/
GET    /api/plugin-config/{name}
PUT    /api/plugin-config/{name}
POST   /api/plugin-config/{name}/reset

GET    /api/security/global
POST   /api/security/global
POST   /api/security/global/freeze
POST   /api/security/global/unfreeze
POST   /api/security/agents/{agent}/network
POST   /api/security/agents/{agent}/filesystem
POST   /api/security/agents/{agent}/sandbox
POST   /api/security/agents/{agent}/plugins
POST   /api/security/agents/{agent}/privacy
GET    /api/security/internal-grants/{agent}
POST   /api/security/internal-grants
DELETE /api/security/internal-grants/{id}
GET    /api/security/trusted-docs
POST   /api/security/trusted-docs
DELETE /api/security/trusted-docs/{host}
GET    /api/security/secrets
POST   /api/security/secrets/canary
POST   /api/security/sandbox/self-test

GET    /api/cost/window/{day|week|month}
GET    /api/cost/events
GET    /api/cost/budgets
POST   /api/cost/budgets
DELETE /api/cost/budgets/{id}

GET    /api/memory/long-term
GET    /api/memory/query
DELETE /api/memory/long-term/{id}
GET    /api/memory/playbooks/{agent}

GET    /api/skills/pending
GET    /api/skills/approved/{agent}
POST   /api/skills/reviews/{id}
POST   /api/skills/disable/{id}

GET    /api/replay/{run_id}
GET    /api/stats/
GET    /api/guardrails/
GET    /api/annotations/
POST   /api/annotations/
DELETE /api/annotations/{id}

GET    /api/audit/
GET    /api/ops/health
GET    /api/ops/data-residency
GET    /api/ops/plugins
POST   /api/ops/validate/agent
POST   /api/ops/validate/task

GET    /api/stream/events   (SSE)
GET    /api/stream/logs     (SSE)
```

---

## Security headers

Every response carries:

```
Content-Security-Policy: default-src 'self'; img-src 'self' data:; ...; frame-ancestors 'none'
X-Frame-Options: DENY
X-Content-Type-Options: nosniff
Referrer-Policy: no-referrer
Cross-Origin-Opener-Policy: same-origin
Cross-Origin-Embedder-Policy: require-corp
Cross-Origin-Resource-Policy: same-origin
Permissions-Policy: geolocation=(), microphone=(), camera=(), usb=(), payment=()
Strict-Transport-Security: max-age=31536000; includeSubDomains  (HTTPS only)
```

HSTS is only set when the request scheme is HTTPS (direct TLS or `X-Forwarded-Proto: https`). Localhost HTTP deliberately avoids HSTS so the browser doesn't cache it.

No inline scripts. No `eval`. Not iframe-embeddable.
