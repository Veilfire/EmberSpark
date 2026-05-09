# Template: `fact-checker`

Takes a claim and produces an evidence-backed verdict. Searches the web,
fetches top sources, extracts main content, and writes a structured
markdown report with citations.

## Required plugins

| Plugin | Purpose |
|---|---|
| `web_search` | Find sources |
| `http_tool` | Fetch + extract article text |
| `markdown_writer` | Write the report |

## Required secrets

- `anthropic_key` (or whichever provider)
- `web_search_key` (except for `ddg_html`)

## Install

```bash
spark template install fact-checker
```

Then configure the three plugins. Run with a specific claim via:

```bash
spark task run ~/.spark/tasks/fact-checker.yaml \
  --agent ~/.spark/agents/fact-checker.yaml
```

Edit `task.yaml`'s `objective` field to change the claim.
