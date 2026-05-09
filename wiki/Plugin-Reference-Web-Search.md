# Plugin Reference: `web_search`

Provider-agnostic web search. Operators pick one of five providers; the plugin normalizes every provider's response into the same `SearchResult` schema.

- **Required permissions:** `net.http`, `secrets.read`
- **Required secrets:** one — the provider API key, resolved via `api_key_secret`
- **Sensitivity:** `MODERATE`
- **Network:** required
- **Dependencies:** `httpx` (already in core deps)

---

## What the plugin does

Wraps the web-search HTTP APIs for Brave, Serper, Tavily, DuckDuckGo HTML, and Bing. For each provider, the plugin:

1. Builds the provider-specific request (query params, headers, body).
2. Runs the request through the same SSRF defense as `http_client` — host allowlist pinned to the provider's hostname, IP validation, and `pin_dns(target)` so the URL keeps its hostname (preserving SNI / TLS cert verification) while DNS is locked to the pre-validated IP for the duration of the call.
3. Parses the response into a uniform `list[SearchResult(title, url, snippet, published_at)]`.

The agent does not know or care which provider is behind the call — it supplies a query, the plugin returns results.

---

## Configuration fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `provider` | `brave` \| `serper` \| `tavily` \| `ddg_html` \| `bing` | `ddg_html` | Which provider's API to call. The default needs no API key — switch to a paid provider for production-grade results. |
| `api_key_secret` | string | `web_search_key` | Keyring secret holding the API key. Ignored for `ddg_html` (no auth). |
| `max_results` | int | `10` | Per-call ceiling on results returned. |
| `safe_search` | `off` \| `moderate` \| `strict` | `moderate` | Provider-specific safe-search level. |
| `connect_timeout_seconds` | float | `5.0` | |
| `read_timeout_seconds` | float | `15.0` | |
| `user_agent` | string | `spark-runtime-web-search/0.1` | Operator-only. |

---

## What the model sends per call

```json
{
  "query": "latest news on langgraph 1.1",
  "max_results": 5,
  "language": "en",
  "country": "us"
}
```

- `query` (required) — the search query, max 500 chars
- `max_results` — clamped to the operator's `max_results`
- `language`, `country` — provider-specific optional refinements

Returns:

```json
{
  "provider": "brave",
  "query": "latest news on langgraph 1.1",
  "result_count": 5,
  "results": [
    {"title": "...", "url": "https://...", "snippet": "...", "published_at": "3 days ago"}
  ]
}
```

---

## Operator workflow

**Pick a provider first.** The tradeoffs:

- **DuckDuckGo HTML** — no API key needed. Quality is lower; results are scraped from the HTML page. **The shipping default** so a fresh install can search the web immediately.
- **Brave** — fast, quality results, reasonable free tier.
- **Serper** — Google results wrapper, excellent quality, paid.
- **Tavily** — LLM-optimized, returns AI-ready snippets, paid.
- **Bing** — Microsoft's search API. Enterprise-friendly pricing.

**Store the API key:**

```bash
spark secrets set web_search_key   # prompts for value (no echo)
```

**Configure the plugin:**

1. Open **Plugins** in the web UI.
2. Select `web_search`.
3. Set `provider`, optionally rename `api_key_secret` if you use a different keyring entry name.
4. Save with a reason.

**Grant the agent:** in the agent YAML, add `web_search` to `plugins.allow` and `net.http` + `secrets.read` to `permissions.grants`.

---

## Common pitfalls

- **Missing API key** — for any provider except `ddg_html`, the plugin raises `PermissionError` if the secret is not in the keyring.
- **DDG HTML parsing drift** — DuckDuckGo occasionally changes their HTML layout. If parsing fails silently, switch providers.
- **Rate limiting** — most providers rate-limit by API key. If you see intermittent failures, check the provider's quota.

---

## Further reading

- [Plugin Reference: http_tool](Plugin-Reference-HTTP-Tool) — for follow-up fetches on the search results
- [Using Plugins](Using-Plugins) — operator workflow for all built-ins
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md) — source-level reference
