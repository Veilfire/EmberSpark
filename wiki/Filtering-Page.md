# Filtering page

**SECURE → Filtering** is the operator surface for the data-class
guardrail engine. It exposes — in one consolidated view — every knob
that previously hid behind code edits or a flat policy table:

- per-category **enforcement level** (allow / warn / redact /
  shadow-block / block)
- per-category **scope set** (which boundaries the level applies to)
- per-category **mask style** (how a redacted match is rendered —
  full mask, last-4, initials, hash, strip, ...)
- per-category **min confidence** floor and **consensus** requirement
- per-detector **enable/disable** in an Advanced drawer
- a **dry-run sandbox** that shows what the resolved policy would do
  to arbitrary text without persisting anything

Configuration of data-class levels, scopes, mask styles, and
per-detector toggles previously lived under
**Security Center → Data Classes**. That tab was removed; every knob
— including time-bound grants — now lives on this page.

---

## Anatomy of the page

### Header

The right-hand toolbar holds four actions:

- **Grants** — opens the time-bounded carve-outs drawer. Lists every
  active `DataClassGrant`, their TTL, and a Revoke control; the **+ New
  grant** form takes a typed-confirm step (must retype the agent name)
  before it can submit. Audited at `critical` severity. Failure-Inspector
  deep-links from a `DATA_CLASS_BLOCKED` error open this drawer
  pre-filled with the matching class + agent + scope.
- **Dry-run** — opens the sandbox modal at the bottom of this page.
- **Discard** (visible only with unsaved changes) — drops every pending
  edit without writing.
- **Save N** — persists every dirty card in one batch. Each category
  write is one audit row at `elevated` severity with the diff.

### Family sections

Categories are grouped into five families purely for layout — the
engine knows only `DataClass`. The order is stable:

| Family | Members |
|---|---|
| 🪪 PII | `pii.basic`, `pii.name`, `pii.gov_id`, `pii.medical` |
| 💳 Financial | `financial.card`, `financial.bank`, `financial.crypto` |
| 🔐 Credentials | `credentials.api`, `credentials.pem`, `secrets.vault` |
| 💻 CLI safety | `cli.destructive`, `cli.privilege`, `cli.pipe_exec`, `cli.exfiltration` |
| 🧠 Prompt safety | `prompt.injection` |

### Category card

Each card has a level chip, a current-vs-default summary, and four
edit controls:

| Control | What it does | Where it's stored |
|---|---|---|
| **Level** dropdown | Enforcement level when a hit fires in any covered scope. | `data_class_policies.level` |
| **Mask style** picker | How a redacted match is rendered (see [§ Mask styles](#mask-styles)). | `data_class_policies.mask_style` |
| **Scopes** chips | Which axes the level applies to. Click a chip to toggle. | `data_class_policies.scopes` |
| **Min confidence** slider | Hits below the floor are dropped before level computation. | `data_class_policies.min_confidence` |
| **Consensus** dropdown | Tri-state: inherit, require ≥2 detectors agreeing, single OK. | `data_class_policies.require_consensus` |

A dirty card has a yellow ring. The trailing **Advanced — N detectors
[ M overrides ]** button opens the per-detector drawer.

### Advanced drawer

For one category, lists every registered detector with:

- a human label + `rule_id` + tier badge (`tier1` deterministic /
  `tier2` Presidio NER)
- one-line description of what it catches
- an **Enabled / Disabled** toggle

Disabling a detector here writes a `detector_overrides_json` entry
on the global policy row for that category and audits at `elevated`.
The override drops every hit whose `rule_id` matches before the level
is computed, no matter the scope. Re-enabling clears the entry.

### Dry-run sandbox

Pick a scope + agent (optional), paste sample text, click **Run**:

- `POST /api/filtering/dry-run` runs `apply_guardrails(text,
  agent_name, scope)` exactly the way the engine would, returning the
  redacted output, the hit table, and the resolved policy snapshot.
- A `block` raises a friendly panel with the error code instead of a
  500.
- The sandbox itself is recorded as one `info`-severity audit row
  (`security.filtering.dry_run`). The raw input never appears in audit;
  only `(input_chars, hit_classes, rule_ids)` are kept.

---

## Mask styles

When a category is set to `redact`, the operator picks how the matched
span is rendered:

| Style | Output for `4111-1111-1111-1234` | Output for `Jane Doe` | Use case |
|---|---|---|---|
| `placeholder_class` | `[REDACTED:financial.card]` | `[REDACTED:pii.name]` | Default for credentials, CLI, vault. Class is preserved for downstream forensics. |
| `placeholder_plain` | `[REDACTED]` | `[REDACTED]` | Strip class info — the agent doesn't need to know what kind of secret it was. |
| `last_4` | `****-****-****-1234` | `****Doe` | Cards, IBANs — support workflows still need to identify the entry. |
| `first_4` | `4111-****-****-****` | `Jane****` | BIN inspection without exposing the rest. |
| `initial` | `(falls back to plain)` | `J. D.` | Names — keeps prose readable. |
| `hash_short` | `[#a1b2c3d4]` | `[#9f8e7d6c]` | Log correlation without exposure. Deterministic per match. |
| `strip` | `` | `` | Prompt injection — leaving a trace risks re-injecting the payload. |

**Per-category defaults** are picked to match reviewer intent. The
`Default` option in the picker shows which style applies if the
operator doesn't override:

```
financial.card  → last_4
financial.bank  → last_4
pii.gov_id      → last_4
pii.name        → initial
prompt.injection → strip
everything else → placeholder_class
```

The mask renderer is pure (no I/O) and lives in
[`spark/privacy/mask.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/privacy/mask.py).
The selector preview on the page is rendered server-side using
synthetic samples, so what you see is exactly what the engine produces.

---

## Resolution order

The Filtering page edits the **global** row for each category, plus
the per-category detector overrides. At enforcement time
`resolve_policy()` merges in this order, **first match wins**:

1. **Unlimited grant** (admin-issued, audited critical) — see
   [Data Classification Guardrails](Data-Classification-Guardrails#unlimited-grants).
2. **Agent override** — `data_class_policies` row with `scope_kind=agent`.
3. **Global override** — `data_class_policies` row with `scope_kind=global`. **This is what the Filtering page edits.**
4. **Built-in default** — from
   [`BUILTIN_DEFAULTS`](https://github.com/Veilfire/EmberSpark/blob/main/spark/privacy/guardrails.py).

The new fields (`mask_style`, `min_confidence`, `require_consensus`,
`detector_overrides`) follow the same precedence per field
*independently*. So an agent can override the level without losing the
global mask style, etc. `require_consensus` is tri-state: setting an
agent row to `null` means inherit from global / default.

---

## REST API

Every mutation writes a `security.filtering.*` audit row at `elevated`
severity (mirrors the precedent set by `security.data_class.*`):

```
GET    /api/filtering/policy
PUT    /api/filtering/policy/category/{data_class}
PUT    /api/filtering/policy/agent/{agent_name}/{data_class}
DELETE /api/filtering/policy/agent/{agent_name}/{data_class}
PUT    /api/filtering/policy/category/{data_class}/detector/{rule_id}
POST   /api/filtering/dry-run
```

`GET /policy` returns one snapshot the page renders in a single pass:
families + categories (with `default_level` / `default_scopes` /
`default_mask_style` / `default_min_confidence` / built-in
`require_consensus` flags), the live `global_override` per class, the
per-class detector catalog, the `agent_overrides` map, and the full
list of mask styles each rendered through every category's preview
sample.

The dry-run endpoint accepts `{text, agent_name?, scope}` and returns:

```json
{
  "blocked": false,
  "input": "<verbatim>",
  "output": "<post-redaction>",
  "hits": [
    {
      "data_class": "financial.card",
      "rule_id": "luhn",
      "tier": "tier1",
      "matched": "4111-1111-1111-1234",
      "confidence": 0.98,
      "start": 16,
      "end": 35
    }
  ],
  "levels_applied": [{"data_class": "financial.card", "level": "redact"}],
  "policy_snapshot": {
    "financial.card": {
      "level": "redact",
      "source": "default",
      "mask_style": "last_4",
      "min_confidence": 0.8,
      "require_consensus": false,
      "scopes": ["memory_write", "model_output", "shell_args", "tool_output", "user_input"]
    },
    ...
  }
}
```

A `block` returns `200` with `{blocked: true, error_code, message,
detail}` so the sandbox UI can render the same shape regardless of
outcome.

---

## Detector catalog

The full registry lives in
[`spark/privacy/detector_catalog.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/privacy/detector_catalog.py).
Each entry is `(rule_id, label, description, tier)`. Rule ids are the
exact strings the classifiers emit on a hit, so toggles in the
Advanced drawer line up byte-for-byte with what
`apply_guardrails` drops.

Highlights:

- **`credentials.api`** — 9 named-vendor regexes (AWS, OpenAI,
  OpenRouter, Anthropic, GitHub, Slack, Stripe, Telegram, JWT) plus
  the high-entropy catch-all. The catch-all has the highest false-
  positive rate; turn it off if the named regexes already cover your
  workloads.
- **`pii.basic`** — six Presidio entity recognizers (email, phone,
  location, IP, URL, date/time). Disabling individual recognizers
  lets you keep email redaction while letting URLs through.
- **`pii.gov_id`** — 4 deterministic patterns (SSN, ITIN, US
  passport) plus 8 Presidio backups (UK NHS, AU ABN/ACN/TFN/Medicare,
  US driver license, ...). The deterministic ones are checksum-aware;
  the Presidio ones are NER-shape matches.
- **`cli.*`** — 25 patterns from `cli_patterns.py`. The most common
  toggle here is `cli.sudo` — operators who run `sudo` legitimately on
  their own host want to keep tool outputs from being scanned for it.
- **`prompt.injection`** — 5 family-level patterns. Disabling all of
  them is equivalent to setting the category level to `allow`.

Adding a detector requires both a classifier change and an entry in
the catalog — the page only renders entries that exist there.

---

## Verification recipes

### Card last-4 in a chat reply

1. Set `financial.card` to `redact` and `Mask style → Reveal last 4`.
   Save.
2. Send a chat message containing `4111-1111-1111-1234`.
3. The assistant's reply (after `apply_guardrails(MODEL_OUTPUT)`)
   shows `****-****-****-1234`. Memory + audit see the same.

### Disable the AWS detector

1. Open `credentials.api` Advanced drawer.
2. Toggle **AWS access key** off.
3. Send a chat message containing `AKIAIOSFODNN7EXAMPLE`.
4. The named-rule hit no longer fires; the high-entropy catch-all may
   still hit (it shares the category). To pass the key fully through,
   disable both.

### Dry-run before saving

1. Edit `pii.basic` → `Min confidence` from 0.55 → 0.85.
2. **Don't** click Save.
3. Click **Dry-run**, paste a paragraph with mixed PII.
4. Note: the dry-run uses the **persisted** policy, not the unsaved
   slider value. Save first, run the sandbox, then iterate. (This is
   intentional — the sandbox is a check on what the engine actually
   does in production, not a what-if simulator.)

### Posture promotion (warn → redact)

1. Set a noisy category (e.g. `prompt.injection`) to `warn`.
2. Watch detections accumulate in `/audit?kind=security.filtering` and
   the existing Guardrails dashboard.
3. After a few days of an acceptable false-positive rate, flip to
   `redact` with `Mask style → Strip`.

---

## Migration from `Security Center → Data Classes`

The Data Classes tab in Security Center is **removed**. Every knob
that used to live there now lives on the Filtering page:

- Levels, scopes, mask styles, min-confidence, consensus, per-detector
  toggles → category cards
- **Grants** → header **Grants** button (drawer with list + new-grant
  form + revoke)
- Per-agent overrides → opening to per-card overrides on the same page

Bookmarks pointing at `/security?tab=data-classes` should move to
`/filtering`.

The underlying tables are unchanged — `data_class_policies` gained
four nullable columns via additive migration, and `data_class_grants`
is unchanged. The Failure Inspector's `DATA_CLASS_BLOCKED` deep-links
now route exclusively to `/filtering` (lower-the-level option) or
`/filtering` with the Grants drawer pre-filled (carve-out option).

---

## Further reading

- [Data Classification Guardrails](Data-Classification-Guardrails) — the engine, taxonomy, grants, scopes
- [Concepts: Privacy](Concepts-Privacy) — the legacy regex/entropy/Presidio chain that runs alongside the data-class system
- [Security Center Guide](Security-Center-Guide) — the surrounding admin surface (network, secrets, sandbox, …)
- [Web UI Guide](Web-UI-Guide) — sidebar layout
- [Error Codes](Error-Codes) — `SPK_E_DATA_CLASS_BLOCKED` payloads
