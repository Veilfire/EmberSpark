# Data Classification Guardrails

**Data Classification Guardrails (DCG)** is EmberSpark's content-aware policy layer. It detects sensitive or dangerous content as it crosses any of the agent's boundaries — tool outputs, chat messages, model responses, memory writes, shell arguments — and applies a per-class **level** (`allow` / `warn` / `redact` / `block`) you pick.

The system lives in **Security Center → Data Classes**.

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
allow   — detect, count, move on. Useful when you want metrics without enforcement.
warn    — detect, audit at info severity, pass through.
redact  — replace each hit inline with `[REDACTED:<class>]`.
block   — raise SparkError(DATA_CLASS_BLOCKED); the operation aborts; audited critical.
```

The level is applied to whichever **scope** the content is currently crossing. A class can have different levels in different scopes — e.g. `financial.card` might be `redact` on tool output but `block` on user input.

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

The resolver is a pure function; resolutions are cached in-process and invalidated on every policy/grant mutation.

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

**Security Center → Data Classes** has four panels:

### 1. Global policy

A table with every built-in class, its description, the current level, the scopes covered, and a source badge (`default` / `override`). Click **Edit** to change level and scope set. Selecting `block` as the level triggers an extra warning confirmation.

### 2. Per-agent overrides

Pick an agent; see each class with its effective level. You can **Set override** to diverge from the global policy, or **Revert** to fall back. Rows that inherit the global value are marked "inherits global/default".

### 3. Unlimited grants

Lists every active grant with agent, class, scopes, expiry, and revoke action. **New grant** opens a modal with typed-name confirmation, scope checkboxes, TTL input, and a "Permanent" opt-in (which triggers an extra danger-tone dialog).

### 4. Recent detections (last 24h)

Horizontal-bar rollup of audit events by class. Auto-refreshes every 30 seconds. Useful for calibration — turn on `warn` for a class in preview mode, watch the hit rate, then promote to `redact` or `block` once the FP rate is acceptable.

---

## REST API

Every mutation is audited under `kind=security.data_class.*`; grants at `critical`, policy edits at `elevated`.

```
GET    /api/security/data-classes                 # taxonomy + defaults
GET    /api/security/data-policy                  # global + per-agent map
PUT    /api/security/data-policy/global/{class}   # admin
PUT    /api/security/data-policy/agent/{agent}/{class}
DELETE /api/security/data-policy/agent/{agent}/{class}   # revert

GET    /api/security/data-grants
POST   /api/security/data-grants       # admin, typed-name confirm, TTL
DELETE /api/security/data-grants/{id}

GET    /api/security/data-detections?hours=24
```

---

## Notifications

Two new kinds, toggleable in **Settings → Notification categories**:

- **`DATA_CLASS_BLOCKED`** — a guardrail refused an operation. Fires once per block event.
- **`DATA_CLASS_GRANT_EXPIRING`** — a non-permanent grant is within 24 hours of expiry.

Both route through the existing notification bell and appear in the per-kind toggle matrix.

---

## Recipes

### "Allow this agent to handle credit cards"

1. **Security Center → Data Classes → Unlimited grants → New grant**
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

- [Concepts: Privacy](Concepts-Privacy) — the pre-DCG privacy pipeline (still the first stage of redaction)
- [Security Center Guide](Security-Center-Guide) — the surrounding UI
- [Error Codes](Error-Codes) — `SPK_E_DATA_CLASS_BLOCKED` and remediation hints
