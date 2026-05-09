# Concept: Privacy & Redaction

EmberSpark's privacy subsystem is **on by default**. You cannot accidentally log a secret, expose PII to the model, or store a raw transcript — each of these requires explicit opt-in that writes an elevated or critical audit entry.

The privacy layer has two cooperating engines:

1. The **legacy redaction pipeline** documented on this page (regex + entropy + Presidio NER → `[REDACTED:LABEL]`). It runs unconditionally on every log event and every promoted memory.
2. The **Data Classification Guardrails** (DCG) — a content-aware policy layer with named classes, scope axes, mask-style rendering, and per-detector toggles. Operator surface lives at **SECURE → Filtering**. See [Data Classification Guardrails](Data-Classification-Guardrails) and the [Filtering page](Filtering-Page) for the deep-dive.

DCG is a surgical overlay on top of the legacy pipeline. Both run; if a class is `block`-level for the current scope, DCG raises before the legacy pipeline gets to redact, so the operator's policy decision wins.

---

## Privacy modes

Three modes configured per-agent:

| Mode | Redaction chain | Sensitivity gates | Raw logs |
|---|---|---|---|
| **strict** (default) | regex + entropy + Presidio (NER) | Refuses `high`/`restricted` in model context | Off |
| **balanced** | Same three layers | Allows `high` in context | Off (must be explicit) |
| **regex_only** | regex + entropy (skip Presidio) | Balanced gates | Off |

`regex_only` exists for lean installs that don't want the ~500 MB spaCy model — you lose NER-based PII detection but keep the deterministic secret-pattern scrubber.

---

## The redaction chain

Every string that flows through the engine (logs, tool outputs that will reach the model, memory candidates) passes through a four-stage pipeline:

### 1. Secret-pattern regex

Deterministic regex matches against:

- AWS access key IDs (`AKIA...`)
- AWS secret keys (40-char base64)
- OpenAI keys (`sk-...`)
- Anthropic keys (`sk-ant-...`)
- OpenRouter keys (`sk-or-...`)
- GitHub tokens (`gh[pousr]_...`)
- Slack tokens (`xox[baprs]-...`)
- Stripe keys (`sk_live_` / `sk_test_`)
- JWTs (three base64 segments)
- PEM-encoded private keys
- Cloud metadata URLs (`169.254.169.254/*`)

Matches are replaced with `[REDACTED:<LABEL>]` so the downstream consumer (memory, log, model context) sees shape-preserving placeholders.

### 2. High-entropy strings

Any string ≥ 16 characters matching `[A-Za-z0-9_\-+/=]+` is scored with Shannon entropy. If entropy ≥ 4.0 bits/char, it's treated as a token and replaced with `[REDACTED:HIGH_ENTROPY]`. The threshold was lowered from 24 → 16 chars in the security review so short API-key-shaped tokens no longer slip through.

### 3. Presidio NER

Microsoft Presidio's `AnalyzerEngine` runs NER over the text looking for:

- `PERSON` — names
- `EMAIL_ADDRESS`
- `PHONE_NUMBER`
- `CREDIT_CARD`
- `IBAN_CODE`
- `US_SSN`
- `LOCATION`
- `IP_ADDRESS`
- `DOMAIN_NAME`
- `DATE_TIME`

Matches above a threshold (0.5 in strict mode, 0.7 in balanced) are replaced via `AnonymizerEngine`. Presidio is lazy-loaded on first call; `spark doctor check` prewarms it so the first real redaction isn't slow.

### 4. Structural filtering

For tool outputs heading to the model, large strings are truncated (4000 chars in strict mode, 16000 in balanced), drop-listed fields are removed, and the result is tagged with its declared `sensitivity` class.

---

## Where redaction runs

**Always** (can't be disabled):

- **Every log event** — walked by the structlog scrub processor before JSON serialization. `SecretStr` unwrap and tracked-value scrubbing is unconditional.
- **Every promoted memory** — `promotion.promote` runs the redactor on both `summary` and `canonical_text` before writing to Chroma.
- **Every retrieved memory** — `_retrieve_memory_context` re-runs `filter_for_model` on each retrieved summary before adding it to the prompt. (Security review fix — used to skip this step.)
- **Every tool output that has `filter_output_before_model=True`** — default on every built-in plugin.

**Off by default** (can be enabled with an elevated audit entry):

- **Raw prompt logging** (`logging.raw_prompts: true`) — writes the assembled system+user messages to the log. Strongly discouraged.
- **Raw model output logging** (`logging.raw_model_outputs: true`) — writes the model's response to the log. Strongly discouraged.

---

## Redaction summaries

Every 60 seconds, the aggregator emits a `redaction.summary` event:

```json
{
  "event_type": "redaction.summary",
  "window_seconds": 60.0,
  "categories": {
    "OPENAI_KEY": 3,
    "JWT": 1,
    "HIGH_ENTROPY": 12,
    "PERSON": 2
  }
}
```

**Counts only, never samples.** The redacted content never lands in the summary. This gives you observability into what the pipeline is scrubbing without giving up the content.

The Guardrails dashboard aggregates these over 24 hours.

---

## What this does NOT protect against

- **A hostile operator.** If you set `raw_prompts: true` and `raw_model_outputs: true` and run a task, the raw content lands in the log. EmberSpark audits it but can't un-log it.
- **False negatives in Presidio.** NER is not perfect. It misses names it doesn't recognize, addresses it doesn't parse, etc. The deterministic regex layer catches known-shape secrets regardless.
- **Content written to disk by plugins.** If a plugin reads a file and writes another file, whatever was in the source file is in the destination. Use `filesystem.read_only: true` or narrow `allow_paths` to prevent this.
- **Leaks in model responses.** The model can include things in its response that bypass the redactor (we don't run redaction on model output by default). If you're paranoid, turn on `raw_model_outputs` briefly to see what's happening.

---

## Sensitivity classes

Every tool plugin declares a `sensitivity`:

- **LOW** — public or non-sensitive content (e.g. `markdown_writer` — markdown files you're writing)
- **MODERATE** — default for most plugins — content may contain PII or secrets
- **HIGH** — content typically contains sensitive data (e.g. a secrets-reading plugin would use this)
- **RESTRICTED** — content must never reach the model (e.g. a plugin that reads raw credentials)

The sensitivity class + privacy mode together determine whether content can:

- Reach the model context
- Be stored in long-term memory
- Appear in logs (even after redaction)

In `strict` mode, `RESTRICTED` content is blocked from all three. In `balanced`, `HIGH` is allowed in memory.

---

## Further reading

- [Data Classification Guardrails](Data-Classification-Guardrails) — the policy-aware overlay (named classes, levels, scopes, grants)
- [Filtering page](Filtering-Page) — operator surface for category cards, mask styles, per-detector toggles, dry-run
- [Concepts: Memory](Concepts-Memory) — how sensitivity gates retrieval
- [docs/logging-and-tracing.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/logging-and-tracing.md) — redaction summary events and log structure
- [docs/security-posture.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/security-posture.md) — threat model
