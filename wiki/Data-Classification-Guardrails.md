# Data Classification Guardrails

**Data Classification Guardrails (DCG)** is EmberSpark's content-aware policy layer. It detects sensitive or dangerous content as it crosses any of the agent's boundaries — tool outputs, chat messages, model responses, memory writes, shell arguments — and applies a per-class **level** (`allow` / `warn` / `redact` / `shadow_block` / `block`) you pick.

The operator surface lives at **SECURE → Filtering** (see the dedicated [Filtering page](Filtering-Page) guide). Per-agent overrides and time-bound grants stay on **Security Center**.

---

## Why it exists

Before DCG, EmberSpark had:

- A coarse `privacy_mode` (strict / balanced / regex_only) that applied the same redaction pipeline to every plugin's output.
- A 4-level `Sensitivity` gate that decided whether content could reach the model, long-term memory, or logs.

That was enough to keep API keys out of logs but too blunt for real workflows:

- No way to say "block credit cards everywhere **except** the CC-processing agent".
- No detection of dangerous shell shapes (`rm -rf /`, `sudo`, `curl | sh`).
- Chat bypassed the filter pipeline entirely — anything you typed flowed straight into the model.

DCG is a surgical overlay on top of the old pipeline. Everything the existing redactor did still works; DCG adds **named classes**, **level semantics**, **scope axes**, and **explicit grants** so policy is both stricter and more flexible.

---

## The taxonomy

Fifteen built-in classes, namespaced by family:

| Class | Default | Detector |
|---|---|---|
| `pii.basic` | `redact` | Presidio (email, phone, address) |
| `pii.name` | `allow` | Presidio (PERSON) — too noisy to enforce |
| `pii.gov_id` | `block` | SSN with area-code filter, ITIN, US passport + Presidio backup |
| `pii.medical` | `block` | Presidio MEDICAL_LICENSE |
| `financial.card` | `block` | Luhn-validated PAN (13–19 digits) |
| `financial.bank` | `block` | IBAN checksum, US routing ABA, SWIFT/BIC |
| `financial.crypto` | `redact` | Presidio CRYPTO |
| `credentials.api` | `redact` | Provider-specific regex + entropy fallback |
| `credentials.pem` | `block` | `-----BEGIN PRIVATE KEY-----` markers |
| `secrets.vault` | `redact` | Exact match against `SecretManager.known_values()` |
| `cli.destructive` | `block` | `rm -rf /`, `dd`, `mkfs`, `shred`, fork-bomb, recursive root chown |
| `cli.privilege` | `block` | `sudo`, `su`, `doas`, `chmod 777`, setuid bits |
| `cli.pipe_exec` | `block` | `curl \| sh`, `wget \| bash`, `iwr \| iex`, `eval base64…` |
| `cli.exfiltration` | `warn` | `nc -e`, reverse-shell bash, outbound `scp` |
| `prompt.injection` | `warn` | "Ignore previous instructions", role-flip, jailbreak markers |

Operators can extend the CLI catalog in [`spark/privacy/cli_patterns.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/privacy/cli_patterns.py) without touching the classifier code.

---

## Levels

```
allow         — detect, count, move on. Useful when you want metrics without enforcement.
warn          — detect, audit at info severity, pass through.
redact        — replace each hit inline through the configured mask style.
shadow_block  — audit AS IF blocked, but pass through. Calibration tool — flip a class
                here, watch the audit rollup, promote to `block` when FP rate is acceptable.
block         — raise SparkError(DATA_CLASS_BLOCKED); the operation aborts; audited critical.
```

The level is applied to whichever **scope** the content is currently crossing. A class can have different levels in different scopes — e.g. `financial.card` might be `redact` on tool output but `block` on user input.

### Mask styles for `redact`

When a category is at `redact`, the operator picks **how** the matched span is rendered. See the [Filtering page](Filtering-Page#mask-styles) for the full table; the short version:

| Style | Card example | Why |
|---|---|---|
| `placeholder_class` | `[REDACTED:financial.card]` | Default for credentials/CLI/vault — preserves shape for downstream forensics. |
| `placeholder_plain` | `[REDACTED]` | Strip class info from the agent. |
| `last_4` | `****-****-****-1234` | Cards/IBAN — support workflows still need to identify the entry. **Default for `financial.card` and `financial.bank`.** |
| `first_4` | `4111-****-****-****` | BIN inspection. |
| `initial` | `J. D.` (for names) | **Default for `pii.name`** — keeps prose readable. |
| `hash_short` | `[#a1b2c3d4]` | Deterministic 8-char hash; lets logs correlate without exposure. |
| `strip` | `` (empty string) | **Default for `prompt.injection`** — leaving a trace risks re-injecting. |

The renderer is pure and lives in [`spark/privacy/mask.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/privacy/mask.py).

### Per-class min_confidence + consensus

Every category also carries a `min_confidence` floor and a `require_consensus` flag, both editable from the Filtering page card:

- `min_confidence` — hits below the floor are dropped before level computation. Lets you raise Presidio-heavy categories (e.g. `pii.basic`) past their default 0.55 if you're seeing false positives.
- `require_consensus` — when true, only fused tier-1+tier-2 hits fire (e.g. an LUHN-validated PAN that Presidio also recognizes as `CREDIT_CARD`). Lets you keep noisy classes like `pii.name` on `redact` without flooding the audit log with single-detector false positives.

## Scopes

```
user_input    — incoming chat messages and task arguments
tool_output   — plugin results, before the model sees them
model_output  — the assistant's generation, before persistence / display
memory_write  — candidates entering long-term memory
shell_args    — the assembled argv for shell / http / git plugins
```

Each policy row specifies **which scopes it covers**. The built-in defaults cover all five scopes for most classes; CLI classes default to only `shell_args`, `user_input`, and `model_output` (since a tool output containing a `sudo` reference is usually fine — it's only executing it that matters).

---

## Resolution order

For any `(agent, class, scope)` at runtime, DCG resolves the effective level in this order — **first match wins**:

1. **Unlimited grant** — an active `DataClassGrantRow` for this agent (or `__all__`) that covers the scope. Grant's `level_override` applies.
2. **Agent-specific policy** — a `DataClassPolicyRow(scope_kind="agent", agent_name=...)` that covers the scope.
3. **Global policy** — a `DataClassPolicyRow(scope_kind="global")` that covers the scope.
4. **Built-in default** — from the table above.

`mask_style`, `min_confidence`, `require_consensus`, and per-detector overrides follow the same precedence per field **independently**. So an agent override that sets `level=block` keeps the global `mask_style` until the same agent row also sets one. `require_consensus` is tri-state — `null` means "inherit from the next layer", `true` / `false` are explicit overrides.

The resolver is a pure function; today there is no in-process cache (it was removed when a writer→reader race surfaced — for the size of the policy set, the cache wasn't worth the foot-gun). `bump_policy_version()` is kept as an extension point for a future cache-invalidation hook.

## Per-detector overrides

Inside each category card the **Advanced** drawer enumerates every registered detector (`rule_id`, label, tier, description). Disabling a detector here writes a `detector_overrides_json` entry on the global policy row; at enforcement time `apply_guardrails` drops every hit whose `rule_id` matches before level computation, no matter the scope.

The full registry is in [`spark/privacy/detector_catalog.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/privacy/detector_catalog.py). Common toggles:

- `credentials.api → high-entropy` — disable the catch-all if the named-vendor regexes already cover your workloads (lowest false-positive cost).
- `pii.basic → presidio:DATE_TIME` / `presidio:URL` — keep email/phone redaction while letting URLs and dates through.
- `cli.privilege → cli.sudo` — operators on a single-user host who run `sudo` legitimately want their own outputs to stop being scanned for it.

---

## Unlimited grants

A **grant** is a scoped carve-out that bypasses the policy hierarchy. Use it when an agent's job *is* to handle a particular class — e.g. a credit-card processor that has to see PANs.

Grants have:

- **Agent** (or `__all__` for a global grant — rare).
- **Class** (one of the 15 from the taxonomy).
- **Scopes** — which directions the carve-out covers.
- **Level override** — almost always `allow` (i.e., the classifier's hits are ignored), though `warn` is occasionally useful for telemetry.
- **Reason** — required; written to the audit log.
- **TTL** — default 7 days; adjustable from 1 hour to 30 days. "Permanent" grants have `null` expiry and require an additional danger-tone confirmation.

Creating a grant requires **admin role + typed-name confirmation + reason**. Every create is audited at `critical` severity and fires an `INCIDENT` notification to the bell. Grant expiration fires a `DATA_CLASS_GRANT_EXPIRING` notification 24 hours before lapse (toggleable in Settings).

---

## Enforcement points

| Scope | File | Notes |
|---|---|---|
| `tool_output` | [`spark/plugins/tool_runtime.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/tool_runtime.py) | After the legacy `filter_for_model` pipeline |
| `shell_args` | [`spark/plugins/builtins/shell.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/builtins/shell.py) | On joined argv, before `Popen` |
| `user_input` | [`spark/web/api/chat.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/api/chat.py) | On every chat turn, before persistence |
| `model_output` | `chat.py` + [`spark/runtime/engine.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/runtime/engine.py) | After each model response, before user/persist |
| `memory_write` | [`spark/memory/promotion.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/memory/promotion.py) | On candidate summary + canonical text, before embed |

A `block` at any enforcement point raises `SparkError(DATA_CLASS_BLOCKED)` with a detail payload containing:

```json
{
  "classes": ["financial.card"],
  "scope": "tool_output",
  "agent": "fact-checker",
  "matched_rule_ids": ["luhn"],
  "suggest_grant": true
}
```

The chat handler turns this into a friendly error toast; the engine classifies it as a tool error and logs it to the run trace; the memory promotion pipeline converts it into `MemoryRejected`.

---

## The UI

The configuration surface lives at **SECURE → Filtering**. See [Filtering page](Filtering-Page) for the deep-dive; the quick summary:

- **Family sections** (PII / Financial / Credentials / CLI safety / Prompt safety) hold one **category card** each per class.
- **Category card** (front) — level chip, level dropdown, mask-style picker (with a live preview rendered server-side), scope chips, min-confidence slider, consensus dropdown, "Advanced — N detectors" button.
- **Advanced drawer** (per category) — enable/disable each registered detector by `rule_id`.
- **Dry-run sandbox** (header button) — paste sample text + scope + agent, see the redacted output, hit table, and resolved policy snapshot without persisting anything.

The legacy **Security Center → Data Classes** tab was removed entirely. **Unlimited grants**, per-agent overrides, and the dry-run sandbox now live on the Filtering page (header → **Grants** drawer for grants).

---

## REST API

Two namespaces serve different operator surfaces:

### `/api/filtering/*` (Filtering page)

Edits the **global** category settings + per-detector overrides + dry-run. Each mutation is audited at `elevated` severity under `kind=security.filtering.*`:

```
GET    /api/filtering/policy                                      # one-shot snapshot
PUT    /api/filtering/policy/category/{data_class}                # global category edit
PUT    /api/filtering/policy/agent/{agent_name}/{data_class}      # agent override
DELETE /api/filtering/policy/agent/{agent_name}/{data_class}      # revert agent
PUT    /api/filtering/policy/category/{data_class}/detector/{rule_id}  # toggle one detector
POST   /api/filtering/dry-run                                     # info-severity audit
```

### `/api/security/data-*` (Security Center — agent overrides + grants)

Grants at `critical`, policy edits at `elevated`:

```
GET    /api/security/data-classes                 # taxonomy + defaults
GET    /api/security/data-policy                  # global + per-agent map
PUT    /api/security/data-policy/global/{class}   # admin (legacy — Filtering page is preferred)
PUT    /api/security/data-policy/agent/{agent}/{class}
DELETE /api/security/data-policy/agent/{agent}/{class}   # revert

GET    /api/security/data-grants
POST   /api/security/data-grants       # admin, typed-name confirm, TTL
DELETE /api/security/data-grants/{id}

GET    /api/security/data-detections?hours=24
```

Both namespaces back the same `data_class_policies` rows. Use `/api/filtering` for category + mask + detector + dry-run; use `/api/security/data-*` for grants and per-agent overrides until those move too.

---

## Notifications

Two new kinds, toggleable in **Settings → Notification categories**:

- **`DATA_CLASS_BLOCKED`** — a guardrail refused an operation. Fires once per block event.
- **`DATA_CLASS_GRANT_EXPIRING`** — a non-permanent grant is within 24 hours of expiry.

Both route through the existing notification bell and appear in the per-kind toggle matrix.

---

## Recipes

### "Allow this agent to handle credit cards"

1. **SECURE → Filtering → Grants → + New grant** (or click an "Open" button in a `DATA_CLASS_BLOCKED` Failure Inspector — it opens this drawer pre-filled).
2. Agent: `cc-processor`
3. Class: `financial.card`
4. Scopes: `user_input, tool_output, model_output, memory_write` (skip `shell_args` — that's for CLI patterns)
5. Level override: `allow`
6. Reason: "Agent ingests cleared transaction records from internal API."
7. TTL: 720 (30 days) — rotate when the business need is re-verified
8. Type the agent name to confirm, submit.

Subsequent runs for `cc-processor` pass PANs through unredacted. Every other agent still gets the global `block`.

### "Warn-only preview mode before enabling a class"

Flip the class to `warn` globally, leave it for a week, look at the Detections panel. If the hit rate is sane, promote to `redact` or `block`. If there are too many false positives, stay on `warn` or fall back to `allow` and improve the detector.

### "An agent is hitting a block and needs the operator's help"

The agent's error payload carries `suggest_grant: true`. The operator:

1. Clicks into the run's trace or the `DATA_CLASS_BLOCKED` notification.
2. Opens **New grant** with the agent, class, and scope pre-filled (future enhancement).
3. Approves with reason + TTL.
4. The run's next iteration succeeds.

---

## Security posture

- **Defaults are safe.** Out of the box: PANs, SSNs, bank numbers, PEM keys, destructive/privileged/pipe-exec CLI patterns are all `block`. Emails and API keys `redact`. Names `allow` (too noisy). Exfiltration and prompt injection `warn` (signal without false-positive churn).
- **No blanket bypass.** There is no global "disable guardrails" flag. Even a permanent grant is per-class + per-scope + audited critical.
- **Classifiers never log content.** Hits carry `rule_id` + span + confidence only; the matched substring is replaced in memory before any downstream handler sees it.
- **Presidio is optional.** The regex-only classes (`financial.card`, `financial.bank`, `pii.gov_id`, `credentials.*`, `cli.*`, `secrets.vault`) work without Presidio. If the `presidio_analyzer` package isn't installed, only those classes fire.
- **Resolver is pure.** The cache is keyed by `(agent, scope, policy_version)` where the version counter bumps on every config mutation. No stale bypasses.
- **Chat can't skip guardrails.** The chat WebSocket runs the same `apply_guardrails` as the engine — closing the historical bypass.

---

## Further reading

- [Filtering page](Filtering-Page) — the operator surface for everything in this doc (category cards, mask styles, advanced drawer, dry-run sandbox)
- [Concepts: Privacy](Concepts-Privacy) — the pre-DCG privacy pipeline (still the first stage of redaction)
- [Security Center Guide](Security-Center-Guide) — the surrounding UI (per-agent overrides + grants)
- [Error Codes](Error-Codes) — `SPK_E_DATA_CLASS_BLOCKED` and remediation hints
