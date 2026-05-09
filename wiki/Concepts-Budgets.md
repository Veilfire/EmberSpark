# Concept: Budgets

Budgets are EmberSpark's answer to "the agent kept going and burned $80." Every run is bounded at six layers:

1. **Iteration ceiling** — how many planner → act loops
2. **Model-call ceiling** — how many LLM invocations
3. **Tool-call ceiling** — how many tool invocations
4. **Token ceiling** — sum of prompt + completion tokens across the run
5. **Wall-clock timeout** — outer `asyncio.wait_for`
6. **Cost ceiling** — checked *before* the run starts, against persistent per-agent / per-provider / global budgets

Any one of them can stop a run.

---

## Layers 1–5: per-run budgets

Configured in the agent YAML's `runtime` block:

```yaml
spec:
  runtime:
    max_iterations: 12
    max_model_calls: 30
    max_tool_calls: 25
    max_tokens_per_run: 200000   # opt-in; null = unbounded
    max_runtime_seconds: 900
```

And optionally overridden per-task:

```yaml
spec:
  budgets:
    max_runtime_seconds: 300
    max_model_calls: 10
    max_tool_calls: 8
    max_tokens_per_run: 50000
```

The task-level override is a **tighter** ceiling — the runtime always picks `min(agent, task)`.

### Inside the engine

`BudgetGuard` is a tiny object that tracks four counters and raises `BudgetExceeded` when one trips. The engine calls:

- `budget.tick_iter()` — once per loop iteration
- `budget.tick_model()` — once per `_invoke_model`
- `budget.tick_tool()` — once per `ToolExecutor.call`
- `budget.tick_tokens(usage)` — after each model call, given the provider's reported usage (prompt + completion tokens)

The outer `asyncio.wait_for(self._run_loop(state), timeout=max_runtime)` handles the wall clock.

Every tick emits a `budget.tick` event with `kind`, `current`, `limit` so the web UI can show live progress bars.

### Token budget specifics

`max_tokens_per_run` is **opt-in** — `null` (the default) means unbounded for that ceiling, leaving cost budgets as the upper bound. When set, the count is the sum of `prompt_tokens + completion_tokens` reported by the provider across every model call in the run. Trips raise `BudgetExceeded` with code `SPK_E_BUDGET_TOKEN_EXCEEDED`.

Use it as a "deterministic worst case" companion to the cost ceiling: cost budgets are evaluated against pricing tables, but token budgets are evaluated against raw provider counters and don't depend on whether `pricing.py` knows the model.

On exhaustion:

- `BudgetExceeded` is raised
- The run state transitions to `failed`
- The audit log records it
- The Guardrails dashboard picks it up

### Why four counters?

Because they catch different failure modes:

- **iterations** catches a planner stuck in a "re-plan, re-plan, re-plan" loop without acting
- **model calls** catches excessive chatter (and is the best proxy for *number* of calls)
- **tool calls** catches a runaway tool loop — e.g. scraping a pagination that never ends
- **tokens** catches a small number of huge model calls — context-window saturation, runaway prompt construction, or a verbose model that ignores its instructions

One counter alone isn't enough. A planner that never calls tools can still burn iterations. A planner that calls one expensive tool over and over can stay within the iteration count. A planner that makes only three model calls but each consumes 100k tokens stays under model-call ceilings but blows the cost. You want all four.

---

## Layer 6: cost budgets

Cost budgets live in a separate table (`budgets`) and are managed from the Web UI's **Cost & Budgets** page.

Each budget has:

| Field | Meaning |
|---|---|
| `scope` | `global` / `agent` / `provider` |
| `scope_key` | The scope value (agent name, provider name, or `*` for global) |
| `period` | `daily` / `weekly` / `monthly` |
| `limit_usd` | Hard ceiling |
| `soft_alert_usd` | Yellow-flag threshold |
| `hard_stop` | If `true`, runs are refused when the period total exceeds `limit_usd` |

`check_budgets` is called before every run (via `_preflight` in the engine). If any active hard-stop budget has been exceeded for the current period, the run is refused with a `PermissionDenied` and an `elevated` audit entry.

### Per-period accounting

The cost tracker records every completed run's prompt/completion tokens and computes USD cost via the pricing table in [`spark/cost/pricing.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/cost/pricing.py). Pricing is in-repo (USD per million tokens) so changes are tracked in git. Ollama and OpenRouter default to $0.

Aggregates are computed at query time from the `cost_events` table. You can see the full window breakdown in the UI's Cost page — by provider, by agent, by model.

---

## How to think about budgets

**Start conservative, widen if needed.** A new agent with 25 tool calls and $5/day is far easier to reason about than one with unlimited everything.

**Budget exhaustion isn't failure — it's the runtime doing its job.** If a task always hits the ceiling, either the task is misconfigured or the ceiling is too tight. Don't silently raise it — investigate first.

**Use cost budgets for peace of mind.** A $50/month global hard stop means the worst case is $50. You don't need to inspect every run individually if you know the ceiling.

**Use iteration budgets for loop bugs.** A planner that keeps re-planning without making progress is a common failure mode; a tight `max_iterations` catches it fast.

**Use wall-clock timeouts for flaky dependencies.** If your provider sometimes hangs, `max_runtime_seconds: 300` ensures a run never takes longer than five minutes regardless of what the provider does.

---

## Further reading

- [Cost & Budgets](Cost-And-Budgets) — operator guide for the Cost page
- [Scheduling Guide](Scheduling-Guide) — how retries, DLQ, and cost ceilings compose
- [docs/tools-and-permissions.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/tools-and-permissions.md) — the full permission gate including budgets
