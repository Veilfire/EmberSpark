# Plugin Reference: `datetime`

Zero-sensitivity, zero-network, zero-filesystem date/time helpers. The strictest possible sandbox config: no bind mounts, no network, no secrets, no process spawn beyond the sandbox worker itself.

- **Required permissions:** none
- **Required secrets:** none
- **Sensitivity:** `LOW`
- **Network:** not needed
- **Dependencies:** stdlib only (`datetime`, `zoneinfo`)

---

## Why this exists

The #1 most common agent stumble is hallucinating dates. "Today is March 14, 2024" — no, it's not, and the model has no way to know. A dedicated `datetime` plugin gives the agent a canonical time source so it can say `await datetime.now()` and trust the answer.

---

## Configuration fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `default_timezone` | string | `UTC` | IANA timezone used when a per-call arg is `null`. |
| `allow_arbitrary_timezones` | bool | `true` | When `false`, the model can only use a timezone in `allowed_timezones`. |
| `allowed_timezones` | list of strings | `[]` | Operator allowlist when `allow_arbitrary_timezones=false`. |

---

## Operations

### `now`

Returns the current time in the target timezone.

```json
{"op": "now", "timezone_name": "America/Vancouver"}
```

```json
{
  "op": "now",
  "iso_string": "2026-04-14T09:32:11-07:00",
  "epoch_seconds": 1776352331.0,
  "timezone_name": "America/Vancouver",
  "is_dst": true
}
```

### `parse`

Parse an ISO 8601 or RFC 2822 date string.

```json
{"op": "parse", "input": "Mon, 14 Apr 2026 09:32:11 -0700"}
```

### `add`

Add a duration (days / hours / minutes / seconds) to an ISO timestamp.

```json
{
  "op": "add",
  "input": "2026-04-14T09:00:00Z",
  "hours": 3,
  "minutes": 30
}
```

### `diff`

Absolute difference between two ISO times in seconds.

```json
{"op": "diff", "input": "2026-04-14T09:00:00Z", "other": "2026-04-14T12:30:00Z"}
```

```json
{"op": "diff", "difference_seconds": 12600.0}
```

### `to_timezone`

Convert an ISO string from its source timezone to a target.

```json
{
  "op": "to_timezone",
  "input": "2026-04-14T09:32:11-07:00",
  "timezone_name": "UTC"
}
```

### `is_dst`

Report whether a given time is in daylight saving.

```json
{"op": "is_dst", "input": "2026-01-15T12:00:00-08:00", "timezone_name": "America/Vancouver"}
```

---

## Operator workflow

**You almost certainly want the default config.** The only knob worth touching is `allow_arbitrary_timezones`. Leaving it `true` (default) lets the agent convert between any IANA timezone — no reason to restrict this in normal use.

**Timezone allowlist** — if for some reason you want the agent restricted to a specific set (e.g. only your team's timezones), set `allow_arbitrary_timezones: false` and populate `allowed_timezones: ["America/Vancouver", "Europe/Berlin"]`.

**Pairing with other plugins** — this plugin is a friend to:

- `email_sender` (generate an ISO timestamp for the email body)
- `csv_io` (write timestamped rows)
- `scheduler` approvals (agent can report "approved at <now>")

---

## Common pitfalls

- **Unknown timezone** — `zoneinfo` raises `ZoneInfoNotFoundError` for invalid IANA names; the plugin converts this to `PermissionError`.
- **Naive datetimes** — if the input has no timezone info, the plugin assumes `default_timezone`. Be explicit in your ISO strings if it matters.
- **Leap seconds** — not supported (Python's `datetime` isn't leap-second-aware). If you need that precision, don't use an LLM.

---

## Further reading

- [Using Plugins](Using-Plugins) — operator workflow
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md)
