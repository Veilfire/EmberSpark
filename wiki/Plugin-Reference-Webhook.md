# Plugin: webhook (outbound)

| | |
|---|---|
| Direction | Outbound — agent → external system |
| Auth | Optional HMAC-SHA256 signing |
| Permissions | `net.http`, `secrets.read` |
| Sensitivity | moderate |
| Network | yes |

The agent uses `webhook` to push notifications to external systems —
Slack incoming webhooks, Zapier/n8n hooks, generic JSON consumers,
downstream EmberSpark instances. It's the symmetric counterpart to the
inbound webhook trigger (`/api/scheduler/webhooks/{id}`).

For the inbound side — letting GitHub / Slack / Stripe / etc fire
EmberSpark tasks — see [Scheduling Guide](Scheduling-Guide#recipe-webhook-trigger).

## Operator config

```yaml
plugins:
  webhook:
    # Hosts the agent may POST to. Empty list = plugin refuses every
    # request. Add only what's necessary.
    allow_hosts:
      - hooks.slack.com
      - api.zapier.com
    # Optional HMAC-SHA256 signing key (secret name in the age vault).
    # When set, every request body is signed and the digest sent in the
    # signature_header so the receiver can verify authenticity. Receivers
    # verify with spark.utils.auth.verify_hmac_sha256.
    signing_key_secret: webhook_signing_key
    signature_header: X-Spark-Signature-256   # default
    allow_http: false                          # https only
    max_body_bytes: 1000000                    # 1 MB
    user_agent: "spark-runtime/0.1"
```

The agent **cannot** widen `allow_hosts` or change the signing key — those
are operator-only.

## Calling from a planner

```json
{
  "tool": "webhook",
  "args": {
    "url": "https://hooks.slack.com/services/T0/B0/XYZ",
    "payload": {"text": "Daily digest published — see deliverables"}
  }
}
```

The plugin returns:

```json
{
  "status_code": 200,
  "response_body": "ok",
  "signed": true
}
```

## Receiver-side verification (Python)

```python
from spark.utils.auth import verify_hmac_sha256

@app.post("/incoming")
async def incoming(request: Request):
    body = await request.body()
    sig = request.headers.get("X-Spark-Signature-256", "")
    if not verify_hmac_sha256(SECRET, body, sig):
        raise HTTPException(401)
    payload = await request.json()
    ...
```

## Threat model

- **SSRF defense** — every request goes through the same `validate_url`
  gauntlet as `http_client`: IDN normalisation, DNS pinning, refusal of
  RFC1918 / loopback / cloud metadata IPs.
- **Header injection refused** — `Host`, `Content-Length`, `Transfer-Encoding`,
  `Connection`, `Upgrade` etc. are stripped from `args.headers`.
- **Body size** — capped at `max_body_bytes`. Default 1 MB.
- **Methods** — POST and PUT only. No GET / DELETE / PATCH (use
  `http_client` for those).
- **No redirect following** — receivers can't bounce the agent to an
  unexpected host.

## Related

- [Plugin Reference: HTTP Client](Plugin-Reference-HTTP-Client) — for
  read-side HTTP requests.
- [Scheduling Guide § webhook trigger](Scheduling-Guide#recipe-webhook-trigger)
  — for the inbound side.
