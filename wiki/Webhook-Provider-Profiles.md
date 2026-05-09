# Webhook Provider Profiles

The inbound webhook trigger is **deliberately generic** — one endpoint
shape (`POST /api/scheduler/webhooks/{trigger_id}`) that composes three
pluggable concerns:

| Concern | Choices |
|---|---|
| `auth_mode` | `bearer` / `hmac_sha256` / `hmac_sha256_slack` |
| `body_parser` | `json` / `form` / `raw` |
| `event_filter` | dotted-path → expected-value rules over the parsed body |

This page documents the right combination for each major provider.
**Build new integrations by picking a profile, not by writing custom
code** — every provider listed here works without changing
EmberSpark.

---

## GitHub

GitHub signs webhook bodies with HMAC-SHA256 and ships the signature
in `X-Hub-Signature-256: sha256=<hex>`. The body is JSON.

```json
{
  "trigger_id": "github-pr-merge",
  "task_name": "code-review-on-merge",
  "auth_mode": "hmac_sha256",
  "body_parser": "json",
  "payload_forwarding": true,
  "event_filter": {"action": "closed", "pull_request.merged": true}
}
```

In the GitHub repo:

1. **Settings → Webhooks → Add webhook**
2. **Payload URL**: `https://<spark-host>/api/scheduler/webhooks/github-pr-merge`
3. **Content type**: `application/json`
4. **Secret**: paste the cleartext shown by EmberSpark exactly once at
   trigger creation.
5. **Which events?**: "Pull requests" (or "Send me everything"; the
   `event_filter` will discard non-merges).

The agent receives the inbound body as `state.trigger_payload`. To read
the PR number / author / title:

```jinja
{{ trigger_payload.pull_request.number }}
{{ trigger_payload.pull_request.user.login }}
{{ trigger_payload.pull_request.title }}
```

---

## Slack (Events API)

Slack uses HMAC with a timestamp baseline (`v0:<ts>:<body>`) for replay
prevention. Use `hmac_sha256_slack` — it verifies the `X-Slack-Signature`
header AND enforces the 5-minute replay window.

```json
{
  "trigger_id": "slack-mentions",
  "task_name": "slack-responder",
  "auth_mode": "hmac_sha256_slack",
  "body_parser": "json",
  "payload_forwarding": true,
  "event_filter": {"event.type": "app_mention"}
}
```

In Slack at https://api.slack.com/apps:

1. **Event Subscriptions** → **Enable Events**
2. **Request URL**:
   `https://<spark-host>/api/scheduler/webhooks/slack-mentions`
   Slack pings the URL with a `url_verification` challenge. EmberSpark
   auto-handles it — just paste the URL and Slack will say "Verified".
3. **Subscribe to bot events**: `app_mention` (or whatever you want).
4. **Basic Information → Signing Secret**: paste the cleartext shown
   by EmberSpark at trigger creation.

The Slack `event_filter` field uses dotted paths against the body. To
also fire on `message.channels` events, drop the filter (every event
fires) and let your task branch on `state.trigger_payload.event.type`.

---

## Stripe

Stripe uses HMAC-SHA256 over the raw body, with a `t=<timestamp>,v1=<sig>`
header. The current trigger system handles the simple `sha256=<hex>`
form; Stripe's `v1=` form is functionally identical to GitHub's
once you split the comma. Use `hmac_sha256` and configure your Stripe
webhook to send to `…/api/scheduler/webhooks/<id>`. EmberSpark accepts
`X-Signature-Sha256` as an alternate header name in addition to
`X-Hub-Signature-256` — point Stripe's signing secret to your trigger.

For *strict* Stripe verification (parsing `t=` and `v1=`), use a
custom upstream layer (e.g. nginx + lua, or a dedicated proxy) that
splits the header and forwards a clean `sha256=<sig>`. Out of scope
for v1.

```json
{
  "trigger_id": "stripe-events",
  "task_name": "billing-handler",
  "auth_mode": "hmac_sha256",
  "body_parser": "json",
  "payload_forwarding": true,
  "event_filter": {"type": "invoice.payment_failed"}
}
```

---

## Linear

Linear signs with HMAC-SHA256 in `Linear-Signature`. The webhook
handler reads `X-Hub-Signature-256` and `X-Spark-Signature-256`
natively; for Linear's header, configure your upstream proxy (or a
small URL rewriter on EmberSpark) to copy the value over.

```json
{
  "trigger_id": "linear-issue-events",
  "task_name": "triage-bot",
  "auth_mode": "hmac_sha256",
  "body_parser": "json",
  "payload_forwarding": true,
  "event_filter": {"type": "Issue", "action": "create"}
}
```

---

## Vercel / Netlify / generic CI

Both use `X-Vercel-Signature` / `X-Webhook-Signature` with HMAC-SHA256
over the raw body. Same as GitHub. Use `hmac_sha256` and set up the
provider with a fresh trigger.

```json
{
  "trigger_id": "vercel-deploy",
  "task_name": "deploy-watcher",
  "auth_mode": "hmac_sha256",
  "body_parser": "json",
  "payload_forwarding": true,
  "event_filter": {"type": "deployment.succeeded"}
}
```

---

## Twilio

Twilio is the form-urlencoded weirdo. It sends `application/x-www-form-urlencoded`
bodies and signs the **URL + sorted form fields** (not the raw body).
Generic HMAC-over-body verification doesn't apply.

For v1: use `auth_mode: bearer` (Twilio supports HTTP basic auth on
the webhook URL itself; EmberSpark accepts the bearer token in
`X-Spark-Token` so you can configure Twilio to call
`https://<host>/api/scheduler/webhooks/<id>?token=<…>` and use a
small upstream rewriter to copy the query token into the header).
Strict Twilio signature verification is **out of scope**.

```json
{
  "trigger_id": "sms-incoming",
  "task_name": "sms-responder",
  "auth_mode": "bearer",
  "body_parser": "form",
  "payload_forwarding": true,
  "event_filter": {}
}
```

The agent reads form fields directly: `state.trigger_payload.From`,
`state.trigger_payload.Body`, etc.

---

## Generic / hand-rolled (bearer)

For internal automation, CI scripts, and anything you control end-to-end,
just use `auth_mode: bearer`:

```json
{
  "trigger_id": "internal-ci-merge",
  "task_name": "build-validator",
  "auth_mode": "bearer",
  "body_parser": "json",
  "payload_forwarding": true,
  "event_filter": null
}
```

The cleartext token shown at create time goes into `X-Spark-Token`
when you `curl`.

---

## Generic / hand-rolled (HMAC)

When you want signing but you control both sides:

```json
{
  "trigger_id": "internal-payments-feed",
  "task_name": "payment-watcher",
  "auth_mode": "hmac_sha256",
  "body_parser": "json",
  "payload_forwarding": true,
  "event_filter": {}
}
```

Sender signs the body with HMAC-SHA256 and sends the digest in
`X-Spark-Signature-256: sha256=<hex>`. EmberSpark verifies in constant
time. Use this for cross-service signed webhooks where you want
operator-grade auditing (the Spark side logs every fire / verify
failure) but don't have a vendor with its own signing scheme.

---

## What ISN'T covered (and why)

| Provider | Issue | Workaround |
|---|---|---|
| Discord interactions | Ed25519, not HMAC | Add a custom auth_mode (future work) or use a Discord proxy that re-signs as HMAC |
| Stripe `v1=` strict | Stripe's signature format requires parsing `t=...,v1=...` and comparing as `t.body` | Strip in an upstream proxy, or wait for a `hmac_sha256_stripe` mode |
| AWS SNS | Uses RSA signatures with X.509 certs | Adapter layer; out of scope for v1 |
| Twilio strict | Signs URL + sorted-form-fields, not raw body | Adapter layer; v1 uses bearer |

For all four, the same patterns hold: write a thin adapter that
verifies in the provider's scheme and forwards a clean
HMAC-SHA256-signed body to EmberSpark. The generic webhook handles
the rest.

---

## Decision matrix

```
Does the provider sign with HMAC-SHA256 over the raw body?
├── Yes
│   ├── Sends a timestamp in a separate header for replay defense (Slack-style)?
│   │   ├── Yes  → auth_mode: hmac_sha256_slack
│   │   └── No   → auth_mode: hmac_sha256
│   └── (GitHub, Stripe, Linear, Vercel, Netlify, generic)
└── No
    ├── Form-encoded body (Twilio)?
    │   └── auth_mode: bearer + body_parser: form (with adapter for strict)
    └── Custom signing scheme (Discord Ed25519, AWS SNS RSA)?
        └── Adapter layer required
```

## Related

- [Scheduling Guide § webhook trigger](Scheduling-Guide#recipe-webhook-trigger)
- [Plugin: webhook (outbound)](Plugin-Reference-Webhook)
- [API Reference — triggers](API-Reference)
