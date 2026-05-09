# Plugin Reference: `http_tool`

A richer HTTP client than `http_client`: per-host method matrices, optional per-host response caps, per-host timeouts, and optional readable-content extraction on HTML `GET` responses.

Use `http_tool` when you need:

- Different HTTP methods on different hosts (e.g. full CRUD on one API but read-only on another)
- Article-text extraction from HTML pages (complements `web_search`)
- Per-host timeout tuning (some APIs are slow, some should fail fast)

For a single-host, method-uniform agent, `http_client` is still simpler. They coexist.

- **Required permissions:** `net.http`, `secrets.read`
- **Required secrets:** none (but per-call `secret_headers` resolve from `ctx.secrets`)
- **Sensitivity:** `MODERATE`
- **Network:** required

---

## The per-host matrix

The config's top field is `rules` — a list of `HttpToolHostRule` entries. Each rule pins one hostname to its own method allowlist and timings.

### `HttpToolHostRule` fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `host` | string | required | Exact FQDN. IDN-normalized at match time. The literal `"*"` is a fallback rule used only when no exact-match rule applies — handy for an "anything not otherwise specified gets read-only GET" default. |
| `allowed_methods` | list of `GET`, `POST`, `PUT`, `DELETE`, `PATCH`, `HEAD` | `["GET"]` | The model's per-call method must be in this list. |
| `allow_http` | bool | `false` | Per-host plaintext override (HTTPS only by default). |
| `max_response_bytes` | int \| `null` | `null` → fall back to default | |
| `connect_timeout_seconds` | float \| `null` | `null` → fall back | |
| `read_timeout_seconds` | float \| `null` | `null` → fall back | |
| `extract_main_content` | bool | `false` | On GET HTML responses, run trafilatura extraction and return the article text in the `main_content` field. |
| `note` | string \| `null` | | Operator-only rationale; shows in audit logs. |

### Top-level fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `rules` | list of HostRule | one `*` GET-only rule with 5 MB cap and `extract_main_content: true` | The matrix. The default lets a fresh agent fetch any HTTPS article without operator config; replace with named-host rules for production. |
| `default_max_response_bytes` | int | `10_000_000` | |
| `default_connect_timeout_seconds` | float | `5.0` | |
| `default_read_timeout_seconds` | float | `15.0` | |
| `user_agent` | string | `spark-runtime-http-tool/0.1` | Operator-only. |

---

## Example config

Three hosts, three different method permissions, one with extraction:

```json
{
  "rules": [
    {
      "host": "api.github.com",
      "allowed_methods": ["GET", "POST", "PUT", "DELETE", "PATCH"],
      "note": "Full CRUD for issue + PR management"
    },
    {
      "host": "api.stripe.com",
      "allowed_methods": ["GET", "POST"],
      "note": "Read balances; POST for refunds only"
    },
    {
      "host": "news.ycombinator.com",
      "allowed_methods": ["GET"],
      "extract_main_content": true,
      "note": "Read-only scraping with readability"
    }
  ],
  "default_max_response_bytes": 10000000,
  "user_agent": "my-org-research-bot/1.0"
}
```

---

## What the model sends per call

```json
{
  "method": "GET",
  "url": "https://news.ycombinator.com/item?id=12345",
  "headers": {},
  "extract_main_content": true
}
```

Returns:

```json
{
  "url": "https://news.ycombinator.com/item?id=12345",
  "status": 200,
  "headers": {"content-type": "text/html; charset=utf-8"},
  "body_text": "<html>...raw HTML up to max_response_bytes...</html>",
  "content_type": "text/html; charset=utf-8",
  "main_content": "The extracted article body, 50,000 char max...",
  "truncated": false
}
```

---

## The matching algorithm

1. **IDN normalization** — the URL's hostname is lower-cased and punycoded.
2. **Rule lookup** — find the first `HttpToolHostRule` whose `host` equals the normalized hostname. If no exact match, the first rule with `host: "*"` (if any) is used as a fallback. No match → `PermissionError("host not in any rule")`.
3. **Method gate** — `args.method` must be in the matched rule's `allowed_methods`. Fail → `PermissionError`.
4. **Scheme gate** — HTTPS unless `rule.allow_http: true`.
5. **SSRF defense** — same as `http_client`: IP validation, RFC1918 / loopback / cloud-metadata blocklist, then `pin_dns(target)` to lock DNS to the pre-validated IP without breaking SNI / cert verification.
6. **Execute** — `httpx` issues the request against the pinned IP.
7. **Extract** — if the response is HTML AND (`rule.extract_main_content` OR per-call `extract_main_content` is true) AND method was `GET`, run `trafilatura.extract` on the body.

---

## Operator workflow

**The default config is permissive.** A fresh install ships with one `*` GET-only rule (5 MB cap, main-content extraction on) so an agent can fetch articles immediately. For production, replace it with named-host rules — the SSRF gauntlet (private-IP block-list, cloud-metadata block, HTTPS-only) still applies, but per-host rules let you scope methods more tightly.

**Start with read-only rules.** Add `POST` / `PUT` / `DELETE` only when the agent actually needs them. A three-line rule `{"host": "api.example.com", "allowed_methods": ["GET"]}` is a lot safer than blanket `["GET","POST","PUT","DELETE","PATCH"]`.

**Per-call `extract_main_content` override.** The agent can set this per call. The rule's value is the default; the call can flip it on (or off) for one specific request.

**Relationship to `http_client`.** An agent should pick one or the other in `plugins.allow`. `http_client` is fine for single-API agents. `http_tool` is fine for multi-API agents. You can in theory enable both, but it's cleaner to just pick one.

---

## Common pitfalls

- **Host not in any rule** — the model gets `host not in any rule`. Add a named-host rule (or a `*` fallback), save, retry.
- **Method not in allowed list** — same, different message.
- **Readability returns `None`** — trafilatura couldn't find the article. The raw `body_text` is still there. Fall back to parsing the HTML yourself if needed.
- **IDN mismatch** — the rule must use the punycoded form for internationalized hostnames. Normalize before adding.

---

## Further reading

- [Plugin Reference: http_client](Plugin-Reference-HTTP-Client) — the simpler flat-allowlist sibling
- [Plugin Reference: web_search](Plugin-Reference-Web-Search) — pairs well: search returns URLs, `http_tool` fetches them
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md)
