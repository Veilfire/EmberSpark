# API Reference

EmberSpark's HTTP API is at `/api/*` when the web UI is running. OpenAPI schema is served at `/api/docs`. This page is a quick reference; for request/response schemas, the OpenAPI schema is authoritative.

## Auth

All routes require one of:

- **Session cookie** (`spark_session`) — obtained via `POST /api/auth/login`
- **`X-Spark-Token` header** — the token in `~/.spark/web-token`

The token header grants **admin** role; cookie auth grants **admin** role on login. Roles:

- `viewer` — read everything
- `operator` — edit configs, personas, plugins, budgets, skills
- `admin` — everything + freeze / raw logging / internal grants / webhook triggers / trusted docs

---

## Auth

| Method | Path | Role | Purpose |
|---|---|---|---|
| `POST` | `/api/auth/login` | public | Username + password login; sets session cookie |
| `POST` | `/api/auth/logout` | any | Clear session cookie |
| `GET`  | `/api/auth/me` | any | Current principal (subject + role) |

## Scheduler

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/scheduler/agents` | viewer | List agents |
| `GET` | `/api/scheduler/tasks` | viewer | List tasks |
| `GET` | `/api/scheduler/tasks/{name}` | viewer | Single task summary |
| `GET` | `/api/scheduler/tasks/{name}/full` | viewer | Full task spec — pre-populates the editor modal; reads YAML on disk |
| `POST` | `/api/scheduler/tasks` | operator | Create a task ad-hoc; writes `~/.spark/tasks/{name}.yaml` and registers in DB. Body: `{name, agent, mode, objective, inputs?, schedule?, budgets?, forensic?, auto_start?}`. Audited at `info` (or `elevated` if agent rebinds). |
| `PUT` | `/api/scheduler/tasks/{name}` | operator | Update an existing task in place. Same body shape as POST. Refused (409) while a run is in flight. Renames not supported. Re-schedules unconditionally. |
| `POST` | `/api/scheduler/tasks/{name}/pause` | operator | Pause a task |
| `POST` | `/api/scheduler/tasks/{name}/stop` | operator | Stop a task |
| `GET` | `/api/scheduler/runs` | viewer | List runs (filter by state, task_name) |
| `GET` | `/api/scheduler/runs/{run_id}` | viewer | Single run |
| `GET` | `/api/scheduler/schedules` | viewer | List schedules |
| `POST` | `/api/scheduler/trigger` | operator | Fire a task immediately (background-executes via `execute_task_by_name`) |
| `POST` | `/api/scheduler/simulate` | viewer | Predict fire times for a cron/interval |
| `GET` | `/api/scheduler/approvals` | viewer | List tasks awaiting approval |
| `POST` | `/api/scheduler/approvals/{task_name}` | operator | Approve a paused task |
| `POST` | `/api/scheduler/dlq/{task_name}/ack` | operator | Ack a DLQ'd task |
| `GET` | `/api/scheduler/triggers` | viewer | List webhook triggers — includes `auth_mode`, `payload_forwarding`, `event_filter`, `failed_verify_count`, `locked_until` |
| `POST` | `/api/scheduler/triggers` | admin | Create a webhook trigger. Body: `{trigger_id, task_name, auth_mode: bearer\|hmac_sha256\|hmac_sha256_slack, body_parser: json\|form\|raw, payload_forwarding: bool, event_filter: dict\|null, rate_limit_per_hour}`. Returns the cleartext credential **exactly once** (bearer token or HMAC shared secret depending on mode). See [Webhook Provider Profiles](Webhook-Provider-Profiles) for which combinations match GitHub / Slack / Stripe / Linear / Vercel / Twilio. |
| `DELETE` | `/api/scheduler/triggers/{id}` | admin | Delete a trigger. For HMAC triggers also deletes the vault secret. |
| `POST` | `/api/scheduler/webhooks/{trigger_id}` | public | Fire a webhook trigger. Bearer mode reads `X-Spark-Token`; `hmac_sha256` reads `X-Hub-Signature-256` / `X-Spark-Signature-256` / `X-Signature-Sha256`; `hmac_sha256_slack` reads `X-Slack-Signature` + `X-Slack-Request-Timestamp` (5-min replay window). The Slack `url_verification` challenge handshake is auto-handled. Body parsed per `body_parser` (`json` default; `form` for x-www-form-urlencoded; `raw` for passthrough). With `payload_forwarding=true`, the parsed body lands on `RunState.trigger_payload`. With `event_filter` set, the body must match every dotted-path → expected-value rule. 10 consecutive bad signatures lock the trigger for 15 min; vault locked → 503. |

## Chat

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/chat/sessions` | viewer | List chat sessions |
| `POST` | `/api/chat/sessions` | operator | Create a chat session |
| `GET` | `/api/chat/sessions/{id}/history` | viewer | Get session history |
| `WS`  | `/api/chat/ws/{session_id}` | via cookie or token | Bidirectional chat socket. Client sends `{message, context: ChatContextConfig}`; server streams `{kind: "token"}`, `{kind: "tool"}`, `{kind: "citations", memories: [...]}`, and `{kind: "done"}` frames. `ChatContextConfig` fields: `max_history_messages`, `include_long_term_memory`, `ltm_top_k`, `ltm_min_score`, `include_global`, `pin_memory_ids`, `exclude_memory_ids`, `emit_citations`. |

## Persona

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/persona/` | viewer | List personas |
| `GET` | `/api/persona/active` | viewer | Currently active persona |
| `GET` | `/api/persona/{id}` | viewer | Single persona |
| `POST` | `/api/persona/` | operator | Create |
| `PUT` | `/api/persona/{id}` | operator | Update |
| `POST` | `/api/persona/{id}/activate` | operator | Activate (elevated audit) |
| `POST` | `/api/persona/{id}/preview` | viewer | Render assembled system prompt |
| `DELETE` | `/api/persona/{id}` | operator | Delete (refuses if active) |

## Plugin config

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/plugin-config/` | viewer | List all plugins with config + schema |
| `GET` | `/api/plugin-config/{name}` | viewer | Single plugin config |
| `PUT` | `/api/plugin-config/{name}` | operator | Update config (elevated audit) |
| `POST` | `/api/plugin-config/{name}/reset` | operator | Drop the config row |

## Security

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/security/global` | viewer | Global posture |
| `POST` | `/api/security/global` | admin | Update global posture (elevated or critical) |
| `POST` | `/api/security/global/freeze` | admin | Freeze runtime (critical) |
| `POST` | `/api/security/global/unfreeze` | admin | Unfreeze |
| `GET` | `/api/security/agents/{agent}/overview` | viewer | Per-agent security snapshot |
| `POST` | `/api/security/agents/{agent}/network` | operator | Patch network policy (elevated) |
| `POST` | `/api/security/agents/{agent}/filesystem` | operator | Patch filesystem policy (elevated) |
| `POST` | `/api/security/agents/{agent}/sandbox` | operator | Patch sandbox policy (elevated) |
| `POST` | `/api/security/agents/{agent}/plugins` | operator | Patch plugin allowlist (elevated) |
| `POST` | `/api/security/agents/{agent}/privacy` | operator | Patch privacy mode (critical if raw logging) |
| `GET` | `/api/security/internal-grants/{agent}` | viewer | List active internal grants for an agent |
| `POST` | `/api/security/internal-grants` | admin | Create internal grant (critical) |
| `DELETE` | `/api/security/internal-grants/{id}` | admin | Revoke |
| `GET` | `/api/security/trusted-docs` | viewer | List trusted doc hosts |
| `POST` | `/api/security/trusted-docs` | admin | Add a trusted doc host (elevated) |
| `DELETE` | `/api/security/trusted-docs/{host}` | admin | Remove |
| `GET` | `/api/security/secrets` | viewer | List secret names (never values) |
| `POST` | `/api/security/secrets/canary` | operator | Canary-test a secret (audited info) |
| `POST` | `/api/security/sandbox/self-test` | operator | Run sandbox availability check |
| `GET` | `/api/security/data-classes` | viewer | List built-in data classes + defaults |
| `GET` | `/api/security/data-policy` | viewer | Global + per-agent data-class policy map |
| `PUT` | `/api/security/data-policy/global/{class}` | admin | Set global level + scopes for a class (elevated) |
| `PUT` | `/api/security/data-policy/agent/{agent}/{class}` | admin | Set per-agent override (elevated) |
| `DELETE` | `/api/security/data-policy/agent/{agent}/{class}` | admin | Revert agent override to global |
| `GET` | `/api/security/data-grants` | viewer | List active data-class grants |
| `POST` | `/api/security/data-grants` | admin | Create unlimited grant (typed confirm, TTL, critical) |
| `DELETE` | `/api/security/data-grants/{id}` | admin | Revoke grant |
| `GET` | `/api/security/data-detections?hours=24` | viewer | Rollup of guardrail events by class |

## Filtering (data-class operator surface)

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/filtering/policy` | viewer | One-shot snapshot — families, categories with built-in defaults + global override + detector catalog, agent override map, mask-style options each rendered through every category's preview sample |
| `PUT` | `/api/filtering/policy/category/{data_class}` | admin | Edit one category's level / scopes / mask_style / min_confidence / require_consensus globally. Audited at `elevated` under `kind=security.filtering.category.update`. |
| `PUT` | `/api/filtering/policy/agent/{agent_name}/{data_class}` | admin | Same, scoped to one agent. Same audit kind, target `agent:{name}:{class}`. |
| `DELETE` | `/api/filtering/policy/agent/{agent_name}/{data_class}` | admin | Revert agent override; falls back to global / built-in default. Audited at `elevated` under `security.filtering.category.revert`. |
| `PUT` | `/api/filtering/policy/category/{data_class}/detector/{rule_id}` | admin | Toggle a single detector inside a category. Body `{enabled: bool, threshold?: float}`. Drops every hit whose `rule_id` matches before level computation. Audited at `elevated` under `security.filtering.detector.update`. |
| `POST` | `/api/filtering/dry-run` | viewer | Run `apply_guardrails(text, agent_name, scope)` against arbitrary text without persisting. Returns `{blocked, input, output, hits, levels_applied, policy_snapshot}`. Audited at `info` under `security.filtering.dry_run` — only `(input_chars, hit_classes, rule_ids)` are kept; the raw input never enters audit. |

## Cost

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/cost/window/{day\|week\|month}` | viewer | Period breakdown by provider/agent/model |
| `GET` | `/api/cost/events` | viewer | Recent per-run cost events |
| `GET` | `/api/cost/budgets` | viewer | List budgets |
| `POST` | `/api/cost/budgets` | operator | Create/update (elevated) |
| `DELETE` | `/api/cost/budgets/{id}` | operator | Delete (elevated) |

## Memory

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/memory/long-term` | viewer | List long-term memory index (filter by namespace) |
| `POST` | `/api/memory/long-term` | operator | Manual create — new memory record |
| `PUT` | `/api/memory/long-term/{id}` | operator | Manual edit — summary / sensitivity / retention / tags |
| `DELETE` | `/api/memory/long-term/{id}` | operator | Delete from index + Chroma |
| `GET` | `/api/memory/query` | viewer | Vector query — sensitivity filtered |
| `GET` | `/api/memory/review-queue` | operator | Memories needing attention (pending/quarantined/contradicting/low-confidence) |
| `GET` | `/api/memory/visualize?namespace=X` | viewer | 2D projection coords for the scatter view |
| `GET` | `/api/memory/circles` | viewer | List memory circles |
| `POST` | `/api/memory/circles` | admin | Create a circle |
| `POST` | `/api/memory/circles/{id}/members` | admin | Add agent to a circle |
| `POST` | `/api/memory/long-term/{id}/promote` | admin | Promote a memory to `__global__` (gated by sharing config + sensitivity) |
| `GET` | `/api/memory/playbooks/{agent}` | viewer | Playbook stats for an agent |
| `GET` | `/api/memory/pruning/status` | viewer | Last/next pruning sweep + counts |
| `POST` | `/api/memory/pruning/dry-run` | operator | Compute what would be pruned |
| `POST` | `/api/memory/pruning/execute` | admin | Run the sweep now |
| `GET` | `/api/memory/export?namespace=X` | admin | Export namespace as JSONL |
| `POST` | `/api/memory/import` | admin | Import JSONL (skip-if-exists by canonical hash) |

## Skills

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/skills/pending` | viewer | Pending review queue |
| `GET` | `/api/skills/approved/{agent}` | viewer | Approved skills for an agent |
| `POST` | `/api/skills/reviews/{id}` | operator | Approve/reject a pending skill |
| `POST` | `/api/skills/disable/{id}` | operator | Disable an approved skill |

## Replay / stats / guardrails / annotations / audit / ops

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/replay/{run_id}` | viewer | Run replay payload — metadata, **`result_text`** (planner's final response, markdown), **`summary`** (reflection digest), **`trigger_payload_json`** (raw inbound body if any), **`triggered_by`** (chain lineage), **`deliverables[]`** (files this run produced), **`error_payload`** (parsed `SparkError.to_dict()` when the run failed via a structured exception, else `null`), and the full span list for the flame graph. |
| `GET` | `/api/stats/` | viewer | Rolling 7-day agent metrics |
| `GET` | `/api/guardrails/?hours=24` | viewer | Aggregated security events for the dashboard. Now also returns `category_kinds` mapping each category to its primary audit `kind` so the dashboard can deep-link `/audit?kind=…`. |
| `GET` | `/api/guardrails/offenders?kind=X&hours=24&limit=5` | viewer | Top-N actors and targets for an audit kind in the time window — drives the [Failure Inspector](Failure-Inspector-Guide)'s offenders mini-table. |
| `GET` | `/api/annotations/?kind=X&target_id=Y` | viewer | List operator notes |
| `POST` | `/api/annotations/` | operator | Create a note |
| `DELETE` | `/api/annotations/{id}` | operator | Delete a note |
| `GET` | `/api/audit/` | viewer | Audit log with kind + min_severity filters |
| `GET` | `/api/ops/health` | public | Ops health snapshot |
| `GET` | `/api/ops/data-residency` | viewer | Disk usage + state locations |
| `GET` | `/api/ops/plugins` | viewer | Plugin registry with hashes |
| `POST` | `/api/ops/validate/agent` | operator | Validate an agent YAML blob |
| `POST` | `/api/ops/validate/task` | operator | Validate a task YAML blob |

## Deliverables

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/deliverables/?run_id=...` | viewer | List files in the deliverables volume. With `?run_id=` returns only files linked to that run via the `deliverables` table. Without it, walks the filesystem and enriches each row with `run_id` / `task_name` / `source` / `kind` from the table when known (manually-dropped files appear with `source: external`). |
| `GET` | `/api/deliverables/{path}` | viewer | Download a file. Refuses symlinks and any path that resolves outside the deliverables root. |

## Settings

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/settings/session` | viewer | Current session-timeout config (`timeout_seconds` + `enabled`) |
| `PUT` | `/api/settings/session` | admin | Set `{enabled: bool, timeout_seconds: int \| null}`. Persists in `session_settings` and hot-swaps `AuthState` — takes effect immediately. Audited at `elevated`. |

## SSE streams

| Method | Path | Role | Purpose |
|---|---|---|---|
| `GET` | `/api/stream/events` | viewer | In-process event bus (SSE) |
| `GET` | `/api/stream/logs` | viewer | Live JSONL log tail (SSE) |

## Request / response examples

### Login

```bash
curl -X POST -H "Content-Type: application/json" \
  -d '{"username": "sparrow1234", "password": "tree-song77@Moon"}' \
  http://127.0.0.1:7777/api/auth/login
# -> sets spark_session cookie
```

### Headless token request

```bash
curl -H "X-Spark-Token: $(cat ~/.spark/web-token)" \
  http://127.0.0.1:7777/api/stats/
```

### Create a budget

```bash
curl -X POST \
  -H "X-Spark-Token: $(cat ~/.spark/web-token)" \
  -H "Content-Type: application/json" \
  -d '{
    "budget_id": "monthly-global",
    "scope": "global",
    "scope_key": "*",
    "period": "monthly",
    "limit_usd": 50.0,
    "soft_alert_usd": 40.0,
    "hard_stop": true
  }' \
  http://127.0.0.1:7777/api/cost/budgets
```

### Schedule simulation

```bash
curl -X POST \
  -H "X-Spark-Token: $(cat ~/.spark/web-token)" \
  -H "Content-Type: application/json" \
  -d '{
    "schedule_type": "cron",
    "expression": "0 8 * * 1",
    "timezone": "America/Vancouver",
    "horizon_hours": 336
  }' \
  http://127.0.0.1:7777/api/scheduler/simulate
```

## Further reading

- [docs/web-ui.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/web-ui.md) — source-level reference
- [Web UI Guide](Web-UI-Guide) — the matching UI walkthrough
