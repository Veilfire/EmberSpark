# Failure Inspector

When a gate refuses an operation, EmberSpark used to surface a thin
red error line — `✗ shell: SPK_E_PATH_DENIED ...` — and leave the
operator to guess which page tunes that gate. The **Failure Inspector**
fixes that. Every structured error now carries enough machine-readable
payload to render:

1. **WHICH gate** fired (named family + the stable `SPK_E_…` code)
2. **WHAT element** triggered it (the matched path / host / class /
   permission / agent — pulled from the error's `detail`)
3. **HOW to tune** — one or two ranked tuning options, each a
   deep-link to the right page with the form pre-filled
4. **WHAT it costs** — a one-line risk statement next to each option,
   expandable to a longer hover tooltip

The inspector is purely additive: pre-existing error UX still works
when the error doesn't carry the new `tuning` field.

---

## Where the inspector appears

| Surface | Trigger | Variant |
|---|---|---|
| **Chat** | Tool error, input guardrail block | inline panel beneath the failed turn — click `Why?` to expand |
| **Run Replay** | Run-level failure with a `SparkError` payload | inline panel in the Error section |
| **Audit Log** | Audit row whose `diff` contains a `SparkError` payload | expand the chevron column → inline inspector |
| **Notification Bell** | `gate_*` kind notifications | bell row tagged with a `Gate` chip; the action_url click navigates to the catalogue's lowest-risk tuning option |

Surfaces that don't carry a `SparkError` (legacy plain-string errors,
non-gate audit rows) gracefully degrade — they render the way they
did before.

---

## What the inline panel looks like

```
✗ shell: SPK_E_PATH_DENIED — Path /etc/passwd is outside allow list
[ Why? ]
  ┌─ Filesystem · path denied ────────────────────────────────────┐
  │                                                                │
  │  Path /etc/passwd is outside the agent's allow list.           │
  │                                                                │
  │  Element                                                       │
  │  ─────                                                         │
  │  agent:  researcher                                            │
  │  path:   /etc/passwd                                           │
  │                                                                │
  │  Tune                                                          │
  │  ────                                                          │
  │  ▸ Use a workspace-relative path             [✓ low risk]      │
  │    The agent's scratch dir is already on the allowlist.        │
  │    Stage files there and copy in/out via an explicit step.     │
  │    Risk: None — preserves the allow_paths boundary. ⓘ          │
  │                                                                │
  │  ▸ Add /etc to allow_paths                  [⚠ high risk]      │
  │    Lets researcher's filesystem plugin read/write under /etc.  │
  │    Symlinks still refused at the kernel boundary.              │
  │    Risk: Agent gains access to everything under /etc. ⓘ        │
  │    [ Open Filesystem policy → ]                                │
  │                                                                │
  └────────────────────────────────────────────────────────────────┘
```

The "Open Filesystem policy →" button takes the operator straight to
**Security Center → Filesystem** with the path field pre-populated.
The operator clicks Save manually so the existing audit + typed-name
confirm flows are unchanged.

---

## The remediation catalogue

The catalogue lives in
[`spark/errors/remediation.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/errors/remediation.py).
It's pure (no I/O, no DB), so calling it inside `SparkError.to_dict()`
is cheap and deterministic. Every `ErrorCode` has at least one entry;
new codes can't ship without one (`tests/unit/test_remediation_catalogue.py`
parameterizes over the full enum).

### Catalogue convention

- **Safest first** — options sorted by severity (`low → critical`) so
  the operator sees the workaround before the dangerous knob.
- **Risk is mandatory, never `"None"` for mutating options** — even
  "Add this host" carries a one-line risk like "Agent gains outbound
  reach to that host".
- **Advice-only options** carry `deep_link: null` and have no Open
  button — they explain a workaround that doesn't change config.
- **Hard-blocked gates** (PATH_TRAVERSAL, URL_METADATA_BLOCKED,
  PATH_SYMLINK_REFUSED) get a single critical "by design" option that
  states there's no operator override.

### `TuningOption` shape

```ts
type TuningOption = {
  label: string;             // imperative, ≤60 chars
  description: string;       // 1–2 sentence explanation
  risk: string;              // 1-line risk; first sentence shown by default
  severity: "low" | "medium" | "high" | "critical";
  deep_link: string | null;  // relative URL with ?prefill=<base64>
  prefill: object | null;    // form values to highlight + set
  audit_kind: string | null; // audit kind that'll fire when applied
};
```

### Adding a new code

1. Add it to `ErrorCode` in `spark/errors/codes.py`.
2. Add a default remediation hint to `_DEFAULT_REMEDIATION`.
3. Add a `_opts_*` builder + `_DISPATCH` entry in
   `spark/errors/remediation.py`. **Order options safest-first** —
   advisory (low) options before mutating (medium / high / critical)
   ones. The parameterized
   `test_catalogue_first_option_is_safest` test enforces this with a
   realistic detail payload, so a misordered branch fails loud.
4. Add a target-page prefill kind to `lib/prefill.ts` if the catalogue
   emits a new prefill shape.
5. The parameterized catalogue test will fail loudly until step 3 lands.

### Where the deep-links land today

| Prefill kind | Target page | What pre-populates |
|---|---|---|
| `data_class_level` | `/filtering` | Stages a category-level edit + flashes the matching card |
| `data_class_grant` | `/filtering` (Grants drawer auto-opens) | Pre-fills agent + class + scope; operator types confirm + reason |
| `fs_allow_path` | `/security?tab=filesystem` | Pre-fills the path-add input (page hydration in v2) |
| `fs_max_read_bytes` | `/plugins` | Plugin config max_read_bytes |
| `network_allow_host` | `/security?tab=network` | Pre-fills the allow-host input |
| `network_allow_method` | `/security?tab=network` | Pre-fills the per-host method rule |
| `internal_ip_grant` | `/security?tab=network` | Opens internal-IP grant flow with agent + host |
| `plugin_allow` | `/security?tab=plugins` | Pre-checks the plugin in the agent's allowlist |
| `permission_grant` | `/security?tab=plugins` | Pre-checks the missing permissions |
| `runtime_budget` | `/agents/<name>` | Pre-fills the runtime budget field |
| `cost_budget` | `/cost` | Opens the budget-create form |
| `sandbox_timeout` | `/security?tab=sandbox` | Pre-fills timeout_seconds |
| `home_assistant_grant` | `/plugins?plugin=home_assistant` | Opens the live-introspection editor for the `home_assistant` plugin with the matching domain checkbox / service-matrix cell / `read_only` toggle pre-ticked + flashed amber. Danger domains (`lock` / `camera` / `device_tracker` / `person` / `alarm_control_panel` / `vacuum`) auto-trigger the typed-confirm modal so the operator sees the warning before allowing. |

---

## Notifications for previously-silent gates

Five new `NotificationKind` values cover gate failures that were
previously only visible in the audit log:

| Kind | Fires for |
|---|---|
| `gate_permission_denied` | `PLUGIN_NOT_ALLOWED`, `PERMISSION_MISSING` |
| `gate_budget_exceeded` | `BUDGET_ITER/MODEL/TOOL/TOKEN/WALL_CLOCK_EXCEEDED` (cost hard-stop has its own existing kind) |
| `gate_network_denied` | `URL_DENIED`, `URL_PRIVATE_IP`, `URL_METADATA_BLOCKED`, `METHOD_NOT_ALLOWED`, `RESPONSE_TOO_LARGE` |
| `gate_filesystem_denied` | `PATH_DENIED`, `FILE_TOO_LARGE` |
| `gate_sandbox_failed` | `SANDBOX_TIMEOUT`, `SANDBOX_UNAVAILABLE`, `SANDBOX_EXEC_FAILED` |

### Dedup

A tight loop hitting `PATH_DENIED` 100 times fires the bell **once**,
not 100 times. The dedup key is `(agent, code, target)` over a 5-minute
rolling window — same shape as the existing `DATA_CLASS_BLOCKED`
dedup. Cross the window or change any component → fires again.

### Per-kind toggles

Each new kind has its own opt-in toggle on **Settings → Notification
categories**, default `True`. Operators who only want to see budget
exceedances can disable the other four families.

The `action_url` on each gate notification is the catalogue's
**lowest-risk tuning option's deep_link**, so clicking the title
takes the operator straight to "the page that fixes it most safely".

---

## Guardrails dashboard offenders

`/guardrails` got two upgrades:

- **Category links work**. Previously each category linked to `/audit`
  with no filter; now they deep-link to `/audit?kind=<primary>` so
  the operator lands on a pre-filtered list.
- **Top-N offenders mini-table**. Click the chevron next to a non-zero
  category → the dashboard fetches `/api/guardrails/offenders?kind=…`
  and renders the top-5 actors and top-5 targets in the last 24h. Lets
  an operator answer "which agent is generating the noise?" in one
  click instead of scrolling the audit log.

---

## REST API

```
GET /api/guardrails/                        # category counts (now includes category_kinds)
GET /api/guardrails/offenders?kind=<k>      # top-N actors + targets per kind (NEW)
```

The structured error itself flows through every existing endpoint
that already returns a SparkError-shaped payload — no new routes.
Specifically:

- `WS /api/chat/ws/{session_id}` — `tool_result` frames now carry
  `error_payload: SparkError.to_dict()` alongside the legacy `error`
  string. The `error` (input guardrail block) frame carries
  `error: SparkError.to_dict()`.
- `GET /api/replay/{run_id}` — added `error_payload` (parsed
  SparkError or `null`) alongside the existing `error` string.
- `POST /api/audit/` and SSE `notification.created` — unchanged
  shape; the inspector parses the `diff` / `body` JSON when present.

---

## Implementation files

| File | Purpose |
|---|---|
| [spark/errors/codes.py](https://github.com/Veilfire/EmberSpark/blob/main/spark/errors/codes.py) | `SparkError.to_dict()` extended with `tuning` |
| [spark/errors/remediation.py](https://github.com/Veilfire/EmberSpark/blob/main/spark/errors/remediation.py) | The catalogue (37 codes × 1–3 options each) |
| [spark/errors/notify.py](https://github.com/Veilfire/EmberSpark/blob/main/spark/errors/notify.py) | Dedup'd gate-family notification fan-out |
| [spark/notifications/kinds.py](https://github.com/Veilfire/EmberSpark/blob/main/spark/notifications/kinds.py) | 5 new `gate_*` kinds |
| [spark/persistence/learning_models.py](https://github.com/Veilfire/EmberSpark/blob/main/spark/persistence/learning_models.py) | 5 new bool columns on `NotificationPreferencesRow` |
| [spark/persistence/db.py](https://github.com/Veilfire/EmberSpark/blob/main/spark/persistence/db.py) | Additive `ALTER TABLE` migration |
| [spark/runtime/engine.py](https://github.com/Veilfire/EmberSpark/blob/main/spark/runtime/engine.py) | `_format_run_error` writes JSON to `task_runs.error` for `SparkError` |
| [spark/web/api/replay.py](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/api/replay.py) | Returns parsed `error_payload` alongside `error` string |
| [spark/web/api/guardrails.py](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/api/guardrails.py) | New `/offenders` route + `category_kinds` map |
| [spark/web/api/chat.py](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/api/chat.py) | WS frames carry `error_payload`; gate notify on tool catch |
| [spark/web/frontend/src/components/FailureInspector.tsx](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/components/FailureInspector.tsx) | The reusable component (`inline` + `compact` variants) |
| [spark/web/frontend/src/lib/prefill.ts](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/lib/prefill.ts) | Prefill encoder/decoder + discriminated union |
| [spark/web/frontend/src/pages/Chat.tsx](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/pages/Chat.tsx) | "Why?" toggle beneath failed turns |
| [spark/web/frontend/src/pages/Replay.tsx](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/pages/Replay.tsx) | Inspector in the Error section |
| [spark/web/frontend/src/pages/AuditLog.tsx](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/pages/AuditLog.tsx) | Chevron-expand rows + URL `?kind=` |
| [spark/web/frontend/src/pages/Guardrails.tsx](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/pages/Guardrails.tsx) | Category links + offenders drill-down |
| [spark/web/frontend/src/components/NotificationBell.tsx](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/components/NotificationBell.tsx) | `Gate` chip on `gate_*` rows |
| [spark/web/frontend/src/pages/Settings.tsx](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/pages/Settings.tsx) | 5 new toggles |

---

## Out of scope (today)

- **One-click direct apply.** Every tuning option deep-links to the
  target page; the operator clicks Save manually. A future v2 could
  add one-click for low-severity options behind a feature flag.
- **Prefill hydration on every target page.** The prefill schema +
  encoder are shipped; pages that need to *read* the `?prefill=`
  query param + render the suggestion banner are wired piecewise as
  operator workflows demand.
- **Aggregated digest.** The bell + toaster cover real-time. A
  weekly "top failures" digest can layer on later.
- **Per-plugin error mapping for `PLUGIN_RAISED`.** Today plugin-
  internal failures bucket as the generic catch-all; a future change
  could let plugins declare their own error codes.
