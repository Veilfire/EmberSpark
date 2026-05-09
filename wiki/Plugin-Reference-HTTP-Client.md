# Plugin Reference: `http_client`

SSRF-hardened outbound HTTPS with a mandatory host allowlist, method gate, IP pinning, and automatic secret injection into headers.

- **Required permissions:** `net.http`, `secrets.read`
- **Required secrets:** dynamic (based on `secret_headers` in the call)
- **Sensitivity:** `MODERATE`
- **Network:** required (sandbox keeps network namespace shared)

---

## What the model can do per call

```json
{
  "method": "GET",
  "url": "https://api.github.com/repos/foo/bar",
  "headers": {"Accept": "application/json"},
  "secret_headers": {"Authorization": "Bearer github_pat"},
  "body": null,
  "json": null
}
```

The `secret_headers` field is the mechanism for injecting authentication. The key is the HTTP header name, the value is a **secret name** (not the cleartext). At runtime, the plugin looks up the secret in `ctx.secrets` (which was populated from the secret manager before the sandbox spawned) and substitutes it into the header value. The cleartext never appears in the args, never appears in logs, never reaches the model.

---

## Configuration fields

### `allow_hosts` — list of strings *(required)*

The **only** hosts the plugin will call. Every URL's hostname is IDN-normalized to punycode and compared to this list. An empty list makes the plugin unusable.

Typical values:

- `["api.github.com"]` — GitHub API
- `["api.slack.com", "slack.com"]` — Slack (some endpoints are on the main domain)
- `["core.telegram.org"]` — Telegram bot API
- `["api.anthropic.com"]` — Anthropic direct

**Narrow aggressively.** Every additional host is a new surface.

### `allow_http` — bool

Permit plain `http://` URLs. Off by default. Turn it on only if you need to call an HTTP-only service that you control (e.g. a LAN service). For public internet, never.

### `allowed_methods` — list of `GET/POST/PUT/DELETE` *(operator-only)*

The methods the plugin will actually issue. Default: `["GET"]`. Any other method is refused inside the plugin before the request is built.

Common patterns:

- `["GET"]` — pure read agents
- `["GET", "POST"]` — agents that create items (issues, messages, records)
- `["GET", "POST", "PUT", "DELETE"]` — full CRUD

The model cannot add methods to this list; only the operator can.

### `max_response_bytes` — int

Streaming ceiling for response bodies. The plugin reads the response in chunks and aborts when the byte count exceeds this value. Default: `5_000_000` (5 MB).

Lower this for context-budget-tight agents that only need short JSON bodies. Raise it for agents that process larger documents.

### `connect_timeout_seconds` — float

Socket connect timeout. Default: `5.0`.

### `read_timeout_seconds` — float

Per-chunk read timeout. Default: `15.0`.

### `user_agent` — string *(operator-only)*

The `User-Agent` header added to every request. Default: `spark-runtime/0.1`.

Good practice: identify your agent with something like `myorg-research-bot/1.0` so the service you're calling can attribute traffic.

---

## The SSRF defense — what the plugin does for you

Every outbound call goes through [`validate_url`](https://github.com/Veilfire/EmberSpark/blob/main/spark/utils/net.py) which:

1. **Scheme check** — `https` required (or explicit `allow_http=true`).
2. **Host allowlist** — lowercased, IDN-encoded to punycode, checked against your `allow_hosts`. Homoglyph attacks (`аpple.com` with a Cyrillic "а") are caught because the punycode form doesn't match `apple.com`.
3. **DNS resolution** — `socket.getaddrinfo` returns every candidate IP for the hostname.
4. **IP validation** — each IP is checked against:
   - Loopback (`127.0.0.0/8`, `::1`)
   - RFC1918 private (`10/8`, `172.16/12`, `192.168/16`)
   - Unique local IPv6 (`fc00::/7`)
   - Link-local (`169.254/16`, `fe80::/10`)
   - Multicast, reserved, unspecified
   - **Cloud metadata exact matches** (`169.254.169.254`, `fd00:ec2::254`, Alibaba `100.100.100.200`)
   - IPv4-mapped IPv6 (`::ffff:127.0.0.1` is unwrapped and re-checked)
5. **IP pinning** — the request is sent to the resolved IP directly with the original `Host` header. **No second DNS lookup at connect time**, so DNS rebinding attacks cannot substitute a different IP during the window between check and connect.
6. **No redirects** — `follow_redirects=False` on the httpx client. If redirects are needed, the model has to make separate calls and each one re-validates.
7. **Max body streaming** — the response is read in chunks and aborted at `max_response_bytes`.
8. **TLS verification always on** — `verify=True`, `trust_env=False` (no env-var poisoning of CA bundle).

**None of this is configurable.** These are hard-coded defenses. You can narrow `allow_hosts` further, but you can't turn them off.

---

## Internal-IP access — the exception path

By default, any request that resolves to an RFC1918 address is refused. If you genuinely need to reach an internal service (a homelab server, an internal API on a VPN), you use the **Internal IP grants** feature in the Security Center:

1. Security Center → Network tab
2. Pick the agent
3. **Add an internal grant**:
   - CIDR: `10.0.5.0/24`
   - Reason: free text, audited
   - TTL hours: 1–24 (default 4)
   - Confirm agent name: type the agent name exactly to confirm
4. The grant is written to `internal_network_grants` with an expiration
5. Tool calls reaching an IP in that CIDR from this agent are allowed **for the TTL window**, then automatically denied again

Every internal grant writes a `critical` audit entry. You'll see it in the Incident Banner. This is deliberate — accidentally widening an agent to internal traffic is the kind of thing you want to notice.

---

## Operator workflows

### Workflow 1 — Read-only GitHub research

Agent YAML:

```yaml
spec:
  plugins: { allow: [http_client] }
  permissions:
    network:
      allow_hosts: [api.github.com]
    grants: [net.http, secrets.read]
```

Plugin config:

```json
{
  "allow_hosts": ["api.github.com"],
  "allow_http": false,
  "allowed_methods": ["GET"],
  "max_response_bytes": 2000000,
  "connect_timeout_seconds": 5,
  "read_timeout_seconds": 15,
  "user_agent": "research-bot/1.0"
}
```

Store the GitHub PAT in the age vault:

```bash
spark secrets set github_pat     # prompts for value (no echo)
```

The model can now make authenticated GitHub reads by including `secret_headers: {"Authorization": "Bearer github_pat"}` in its tool call — the plugin resolves `github_pat` via `ctx.secrets` and substitutes the cleartext into the header at request time.

### Workflow 2 — Slack notification sender

Agent YAML grants `net.http`, `secrets.read`. Plugin config:

```json
{
  "allow_hosts": ["hooks.slack.com", "slack.com"],
  "allow_http": false,
  "allowed_methods": ["POST"],
  "max_response_bytes": 100000,
  "user_agent": "notification-bot/1.0"
}
```

The agent can now POST to the Slack webhook URL but can only POST — it can't GET (which is fine for a webhook, which ignores GETs anyway). Add `POST` to `allowed_methods`; the operator made this choice deliberately.

### Workflow 3 — Multi-host API client (carefully)

If an agent genuinely needs to reach multiple hosts, list them all and keep everything else narrow:

```json
{
  "allow_hosts": [
    "api.github.com",
    "api.linear.app",
    "api.notion.com"
  ],
  "allow_http": false,
  "allowed_methods": ["GET", "POST"],
  "max_response_bytes": 5000000
}
```

Each host is an explicit operator decision. Even though the list has three entries, none of them are wildcards — the model can only reach exactly those three.

---

## Common failures and what they mean

### `UrlDenied: host 'evil.example' is not in the allowlist`

The model tried to call a host you haven't approved. This is the SSRF defense working. If this is a legitimate new use case, add the host to the plugin config. If not, ask yourself why the model tried it.

### `UrlDenied: Loopback address 127.0.0.1 blocked`

The resolved IP of the hostname is on the block list. For legitimate internal-IP access, use the Internal IP grants flow in the Security Center. Otherwise, something resolved unexpectedly — check your DNS.

### `UrlDenied: http:// requires explicit opt-in; use https://`

The URL is plaintext HTTP. Either use HTTPS (preferred) or flip `allow_http: true` in the plugin config.

### `PermissionError: http method 'POST' not in allowed_methods ['GET']`

You set `allowed_methods: ["GET"]` but the model tried POST. Either add POST to the list or change the task so it doesn't need POST.

### `PermissionError: secret 'github_pat' not injected into context`

The model referenced a secret that the plugin didn't declare in `required_secrets`. The plugin only injects declared secrets. Either:

- The secret name in `secret_headers` is wrong
- The secret isn't in the age vault (run `spark secrets list` to check)
- The plugin's `required_secrets` doesn't include it (for dynamic `secret_headers`, this is handled at runtime via `ctx.secrets.get`)

Use the Security Center → Secrets tab's **canary test** to verify reachability without exposing the value.

### Response truncated at max_response_bytes

Not an error — just a warning in the output. The plugin set `truncated: true` and returned what it had. Either raise `max_response_bytes` or narrow the request to a smaller endpoint.

---

## Further reading

- [Using Plugins](Using-Plugins) — the operator workflow
- [Plugin Reference: filesystem](Plugin-Reference-Filesystem) — for writing the fetched data to disk
- [Security Center Guide](Security-Center-Guide) — internal IP grants, trusted doc sources for skill discovery
- [docs/security-posture.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/security-posture.md) — the full SSRF defense description
