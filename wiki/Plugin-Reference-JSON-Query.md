# Plugin Reference: `json_query`

JMESPath filter over JSON payloads. Lets the agent extract specific fields from a large API response (e.g. from `http_client` or `http_tool`) instead of dumping the entire blob into the model context.

- **Required permissions:** none
- **Required secrets:** none
- **Sensitivity:** `MODERATE` (output mirrors input)
- **Network:** not needed
- **Dependencies:** `jmespath` (pure Python)

---

## Why this exists

A typical agent flow:

1. Call `http_tool` and get back a 200 KB JSON response.
2. The model only needs three fields from the response.
3. Without `json_query`, the entire 200 KB goes into the next model call — that's a lot of tokens.

With `json_query`, the agent pipes the 200 KB through a JMESPath expression like `.issues[*].{id:id, title:title, state:state}` and gets back a ~2 KB extract.

---

## Why JMESPath and not jq

- **JMESPath** is pure Python (no native deps), Apache 2-licensed, stable for a decade, and has a clean well-documented syntax.
- **jq** needs a native `libjq` dependency which complicates the sandbox setup.

The two languages are similar enough that if you know jq, JMESPath is an hour of learning.

---

## Configuration fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `max_input_bytes` | int | `5_000_000` | Input JSON size cap. Larger inputs are refused. |
| `max_output_chars` | int | `50_000` | Result text cap. Larger outputs are truncated. |

No allowlists — this plugin does not touch the network or the filesystem.

---

## What the model sends per call

```json
{
  "json_blob": "{\"issues\": [{\"id\": 1, \"title\": \"Fix bug\"}, ...]}",
  "query": "issues[*].{id:id, title:title}"
}
```

Returns:

```json
{
  "query": "issues[*].{id:id, title:title}",
  "result": [{"id": 1, "title": "Fix bug"}, ...],
  "result_text": "[\n  {\n    \"id\": 1,\n    \"title\": \"Fix bug\"\n  }\n]",
  "truncated": false
}
```

The `result` field is the raw JMESPath output. The `result_text` field is the same value serialized as indented JSON so the model has something to reason about without re-parsing.

---

## Operator workflow

**You probably don't need to configure anything.** The defaults (5 MB input, 50 KB output) are fine for most use cases.

**Grant the plugin with zero permissions.** `json_query` has no `required_permissions` — no special grants are needed in the agent YAML.

**Use after `http_tool` / `http_client`.** The typical flow is:

1. Agent calls `http_tool` to fetch a JSON endpoint.
2. `http_tool` returns `body_text` — a string.
3. Agent calls `json_query` with `json_blob: body_text` and a narrow JMESPath expression.
4. Agent gets back the extract, operates on it, maybe summarizes.

---

## Common pitfalls

- **Malformed JSON** — if the input isn't valid JSON, the plugin raises `PermissionError` with the parse error message.
- **Invalid JMESPath** — raises `PermissionError` with the compile error.
- **Non-JSON-serializable result** — JMESPath can return Python objects that don't round-trip through `json.dumps`. The plugin falls back to `str(result)` for display; the raw `result` field is still in the response.

---

## Further reading

- [JMESPath tutorial](https://jmespath.org/tutorial.html)
- [Plugin Reference: http_client](Plugin-Reference-HTTP-Client)
- [Plugin Reference: http_tool](Plugin-Reference-HTTP-Tool)
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md)
