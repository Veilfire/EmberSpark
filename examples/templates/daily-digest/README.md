# Template: `daily-digest`

Polls a set of RSS/Atom feeds every morning, summarizes the new items,
writes a markdown digest to the deliverables directory, and emails it.

## Required plugins

| Plugin | Purpose |
|---|---|
| `rss_reader` | Fetch + parse feeds |
| `http_tool` | Follow interesting links for more detail |
| `markdown_writer` | Write the digest file |
| `email_sender` | Ship the digest to your inbox |

## Required secrets

- `anthropic_key` (or whichever provider)
- `smtp_username`, `smtp_password`

## Install

```bash
spark template install daily-digest
```

Edit `task.yaml` to list your feed URLs and `agent.yaml` to set the
recipient domain. Configure the four plugins from the Plugins page.
