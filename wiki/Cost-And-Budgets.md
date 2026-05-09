# Cost & Budgets

EmberSpark tracks token usage per run and lets you set hard-stop budgets at three scopes. This is your defense against "the agent just spent $47 on one run."

## Cost tracking

EmberSpark records cost at two granularities: **per-call** (one row per planner iteration) and **per-run** (one aggregate row per finished run). The dashboard at `/cost` reads the per-run aggregate; the Replay page reads the per-call breakdown.

### Per-call rows (`model_call_events`)

Every model invocation — whether from a scheduled task run **or a chat session** — writes a `model_call_events` row:

- `run_id`, `sequence` (planner iteration), `started_at`, `finished_at`, `latency_ms`
- `provider`, `model`, `request_id` (the provider's response id — `gen-…` for OpenRouter, `msg_…` for Anthropic, `chatcmpl-…` for OpenAI)
- Five token classes: `input_tokens`, `output_tokens`, `cached_input_tokens`, `cache_creation_tokens`, `reasoning_tokens`
- `cost_usd` and `cost_source` — the latter is `computed` (from the local price table) or `reported` (provider-authoritative)
- `raw_metadata_json` — `usage_metadata` + `response_metadata` for forensic spelunking

**Task vs chat** — task runs use the `run-{timestamp}-{rand}` id you see on the Runs page; chat turns use `chat-{turn_id}-{rand}` so they're easy to filter. Both flow through the same `record_model_call` helper in [`spark/cost/per_call.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/cost/per_call.py), so the OpenRouter `computed → reported` flip works identically in chat. The Cost dashboard sums every row regardless of source — chat spend appears alongside task spend in the by-agent / by-model breakdowns.

OpenRouter rows start as `cost_source=computed` and **flip to `reported`** when a deferred `GET /api/v1/generation?id={gen}` enrichment lands (~2 seconds after the call; up to 4 retries on ingestion lag). The Replay page shows a `✓` next to authoritative rows and `≈` next to computed ones, with the `gen-…` request_id deep-linking to OpenRouter's activity dashboard.

OpenAI and Anthropic don't expose a per-request cost API — only org-level daily aggregates — so those rows always stay `computed` from the local price table. Token counts are accurate (LangChain's `usage_metadata`); the USD figure depends on the price table being current.

### Per-run aggregate (`cost_events`)

When a task run finalizes — or a chat turn ends — the runtime sums every `model_call_events` row for that `run_id` and writes a single `cost_events` row:

- `run_id`, `agent_name`, `task_name`
- `provider`, `model` (whichever the run was configured for)
- `prompt_tokens`, `completion_tokens`, `total_tokens`
- `prompt_cost_usd`, `completion_cost_usd`, `total_cost_usd` — sourced from the per-call rows when present, so any OpenRouter enrichment that flipped a row to `reported` flows into the dashboard total
- `recorded_at`

If a run errors before any model call records (e.g., it hits the freeze gate), the legacy in-memory `CostTracker` accumulator + price table is the fallback so the row still lands.

### The pricing table

Costs are computed from an in-repo pricing table at [`spark/cost/pricing.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/cost/pricing.py). Each entry can declare five rates per million tokens — `prompt`, `completion`, `cache_read` (defaults to prompt rate), `cache_creation` (defaults to prompt rate), `reasoning` (defaults to completion rate). The math is:

```
prompt_cost  = (input_tokens − cached − cache_creation)      × prompt_rate    / 1e6
cache_read   = cached                                         × cache_read    / 1e6
cache_create = cache_creation                                 × cache_creation / 1e6
completion   = (output_tokens − reasoning)                    × completion    / 1e6
reasoning    = reasoning                                      × reasoning     / 1e6
```

Current entries:

- OpenAI — `gpt-4.1`, `gpt-4.1-mini`, `gpt-4o`, `o1` (with prompt-cache discount: `cache_read = 50%` of prompt)
- Anthropic — `claude-opus-4-6`, `claude-sonnet-4-6`, `claude-haiku-4-5` (with full prompt-cache schedule: `cache_read = 10%`, `cache_creation = 125%` of prompt)
- OpenRouter — wildcard $0 (the local computed value is a fallback; the deferred enrichment fills the authoritative figure)
- Ollama — wildcard $0 (local, no cost)

Updating the pricing table is a code change — deliberately. It's in git, so pricing drift is a PR.

## The Cost page

Open **Cost & Budgets** in the sidebar. Three panels:

### Period toggle

- **Day** — last 24h
- **Week** — last 7 days
- **Month** — last 30 days

### Breakdowns

Three cards showing total USD for the selected period, broken out by:

- **Provider** — openai, anthropic, openrouter, ollama
- **Agent** — the one agent (for now)
- **Model** — each model sorted by spend

### Budgets

Existing budgets in a table. Create form below:

- **Budget ID** — operator-chosen identifier
- **Scope** — `global` / `agent` / `provider`
- **Scope key** — the agent name or provider name, or `*` for global
- **Period** — `daily` / `weekly` / `monthly`
- **Limit USD** — hard ceiling
- **Soft alert USD** — yellow-flag threshold
- **Hard stop** — if checked, runs are refused when limit is exceeded

### Recent cost events

The last 50 per-run cost events with run_id, agent, provider, model, tokens, cost, and timestamp.

## Budget enforcement

Before every run, `check_budgets(agent, provider)` is called in the engine's `_preflight`. If any active hard-stop budget has been exceeded for its period, the run is refused with `PermissionDenied` and an `elevated` audit entry.

### Matching logic

For each active budget:

- If `scope == "global"`, the total spend for the period is compared to `limit_usd`
- If `scope == "agent"`, only runs matching `agent_name == scope_key` count
- If `scope == "provider"`, only runs matching `provider == scope_key` count

The **tightest** binding wins — if both global and agent budgets are set, both are checked, and either tripping refuses the run.

## Recipes

### Monthly global hard stop

```
budget_id: monthly-global
scope: global
scope_key: *
period: monthly
limit_usd: 50.0
soft_alert_usd: 40.0
hard_stop: true
```

No run ever makes you spend more than $50 in a calendar month. Unplanned cost spikes get caught.

### Daily per-provider soft alert

```
budget_id: daily-anthropic
scope: provider
scope_key: anthropic
period: daily
limit_usd: 5.0
soft_alert_usd: 4.0
hard_stop: false
```

Soft alerts don't stop runs, but the scheduler logs an `elevated` event when the soft threshold is crossed. Useful for catching runs that are unexpectedly expensive without blocking them.

### Weekly per-agent hard stop

```
budget_id: weekly-research
scope: agent
scope_key: research-assistant
period: weekly
limit_usd: 10.0
hard_stop: true
```

Bounds a single agent's weekly cost independent of other agents (if you add more later).

## Things to know

- **Cost events are recorded on completion.** A run in progress doesn't show up in the period total yet. If you're worried about a long-running run blowing the budget mid-execution, use a tighter `runtime.max_runtime_seconds`.
- **Ollama and OpenRouter are $0 by default.** Update the pricing table if you're using OpenRouter and want accurate numbers.
- **Deleting a budget is audited** at `elevated` severity.
- **Budgets are operator-edited in the UI.** There's no YAML field for them — they live in the DB.

## Further reading

- [Concepts: Budgets](Concepts-Budgets) — the conceptual layer
- [Scheduling Guide](Scheduling-Guide) — how budget ceilings gate scheduled fires
- [docs/tools-and-permissions.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/tools-and-permissions.md) — the full permission gate that includes budgets
