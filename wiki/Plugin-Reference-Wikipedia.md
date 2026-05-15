# Plugin: `wikipedia`

Wikipedia search and article extract. Free, no auth required.
Cleaner than `web_search + http_tool` for canonical references —
returns text directly from the Wikipedia REST API without scraping.

| | |
|---|---|
| **Required permissions** | `NET_HTTP` |
| **Sensitivity** | `LOW` |
| **Output filtered** | Yes |

## Actions

| Action | What it returns |
|---|---|
| `search` | Fuzzy title match — up to 50 hits with snippet + URL |
| `summary` | One article's lede + infobox (capped at `max_summary_chars`) |
| `section` | One named section from an article (capped at `max_section_chars`) |

## Bootstrap

No setup — pick a language in plugin config (`en`, `de`, `ja`, `fr`, …) and the agent can query. Default is English.

## Source

- Plugin: [`spark/plugins/builtins/wikipedia.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/builtins/wikipedia.py)
- Tests: [`tests/unit/test_trio_plugins.py`](https://github.com/Veilfire/EmberSpark/blob/main/tests/unit/test_trio_plugins.py)
