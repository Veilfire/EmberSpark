# Plugin Reference: `rss_reader`

Fetch and parse an RSS or Atom feed into structured entries.

Distinct from the scheduler's `http_new_row` event source:

- **`http_new_row`** polls a URL on a cron and fires a task when new items appear. It lives in the scheduler layer.
- **`rss_reader`** is a tool the model calls *during a run*. Use it when an agent wants to poll a feed on demand, summarize it, and return the result.

- **Required permissions:** `net.http`
- **Required secrets:** none
- **Sensitivity:** `MODERATE`
- **Network:** required
- **Dependencies:** `feedparser` (pure Python)

---

## Configuration fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `allow_hosts` | list of strings | `[]` | SSRF-defense hostname allowlist. |
| `max_items` | int | `50` | Per-call entry cap. |
| `include_content` | bool | `true` | Return each entry's parsed content body. |
| `max_content_chars` | int | `5_000` | Per-entry truncation for the content body. |
| `connect_timeout_seconds` | float | `5.0` | |
| `read_timeout_seconds` | float | `15.0` | |
| `user_agent` | string | `spark-runtime-rss/0.1` | |

---

## What the model sends per call

```json
{
  "url": "https://hnrss.org/frontpage",
  "max_items": 20
}
```

Returns:

```json
{
  "feed_title": "Hacker News",
  "feed_link": "https://news.ycombinator.com/",
  "item_count": 20,
  "items": [
    {
      "title": "Show HN: ...",
      "link": "https://news.ycombinator.com/item?id=12345",
      "published": "Mon, 14 Apr 2026 09:00:00 GMT",
      "summary": "...",
      "content": "...",
      "id": "https://news.ycombinator.com/item?id=12345"
    }
  ]
}
```

---

## Operator workflow

**Allowlist the specific feed hosts.** Do not use `*`. A typical config:

```json
{
  "allow_hosts": [
    "hnrss.org",
    "rss.slashdot.org",
    "www.reddit.com"
  ],
  "max_items": 30,
  "include_content": true
}
```

**Per-feed vs scheduler event source.** If you want the agent to *check* a feed during a run, use this plugin. If you want a *task to fire* when new items land, use the scheduler's `http_new_row` event source instead (see [Scheduling Guide](Scheduling-Guide)).

**Large feeds** — some feeds have huge entries with embedded HTML. The `max_content_chars` cap truncates per-entry so a single enormous post doesn't overflow the model context.

---

## Common pitfalls

- **Feed host not in allowlist** — refused by the SSRF layer before any network call.
- **Malformed XML** — `feedparser` is permissive and usually returns something useful even on broken feeds. Check `item_count` to see if parsing succeeded.
- **No `content`, only `summary`** — many feeds only provide a summary, not a full content body. The `content` field will be `null`; use `summary` instead.

---

## Further reading

- [Scheduling Guide](Scheduling-Guide) — the scheduler's `http_new_row` event source
- [Plugin Reference: http_tool](Plugin-Reference-HTTP-Tool) — for non-RSS HTTP
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md)
