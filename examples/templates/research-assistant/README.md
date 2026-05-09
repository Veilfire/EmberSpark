# Template: `research-assistant`

A weekly research digest agent. Searches the web for new items on one or
more topics, fetches the top few results, extracts the article text, and
writes a consolidated markdown digest to the deliverables directory.

**What it does on each run:**

1. For each topic in the task's `objective`, call `web_search` to find
   recent items.
2. For the top results, call `http_tool` with `extract_main_content: true`
   to pull readable article text.
3. Call `markdown_writer` to assemble a single digest file under
   `{deliverables}/research/YYYY-MM-DD.md`.
4. Notification fires → `download_ready` → bell lights up in the UI.

## Required plugins

| Plugin | Purpose | Configure |
|---|---|---|
| `web_search` | Find articles | Pick a provider + set `web_search_key` secret |
| `http_tool` | Fetch + extract article text | Set `rules` with your target hosts |
| `markdown_writer` | Write the digest | `allow_paths` must include deliverables |

## Required secrets

- `anthropic_key` (or whichever provider you point the agent at)
- `web_search_key` (except for the `ddg_html` provider)

Populate with `spark secrets set <name>` after installing the template.

## Install

```bash
spark template install research-assistant
```

Then configure the three plugins from the web UI's **Plugins** page (the
Templates page will auto-navigate you there) and run:

```bash
spark task run ~/.spark/tasks/research-assistant.yaml \
  --agent ~/.spark/agents/research-assistant.yaml
```

Or let the scheduler fire it on the default cron (`0 8 * * 1` — Monday
8 AM America/Vancouver).

## Customization

- Change the topic list by editing `spec.objective` in `task.yaml`
- Change the schedule by editing `spec.schedule`
- Change the provider by editing `spec.runtime.provider` in `agent.yaml`
- Change the budget ceilings by editing `spec.runtime.max_*`
