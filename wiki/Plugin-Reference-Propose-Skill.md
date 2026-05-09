# Plugin Reference: `propose_skill`

Agent-side entry point for skill proposals. Lets the model formally propose a new skill from inside its tool-call loop instead of just describing the idea in chat where it dies. Proposals land in the same `skill_reviews` queue the discovery engine writes to and surface on the **Skills** page; operator approves or rejects with the existing UI.

For the conceptual model, see [Concepts: Skills](Concepts-Skills) and [Skill Catalog](Skill-Catalog).

## Mechanics

- **Allowlist:** add `propose_skill` to the agent's `spec.plugins.allow`.
- **Permissions:** none required. The plugin's only side effect is a typed insert into the runtime DB (`skill_reviews` + `audit_log`) plus an in-process notification fan-out — no network, no shell, no filesystem writes outside the runtime DB.
- **Sandbox:** opts out of the bwrap sandbox (`runs_in_sandbox = False`). Runs in-process in the parent because the sandbox's filesystem isolation deliberately blocks DB access. See `spark.plugins.tool_runtime._InProcessCtx`.
- **Sensitivity:** LOW. `filter_output_before_model = True`.

## What the model sends per call

```json
{
  "name": "claim_decomposition",
  "description": "Break complex multi-part claims into atomic sub-claims before fact-checking each one independently.",
  "rationale": "Right now I tackle claims holistically; a structured decomposition would make my verdicts more rigorous and traceable.",
  "kind": "behavior",
  "examples": [
    "Claim 'X is illegal because Y' → split into legal-status sub-claim + causal-link sub-claim.",
    "Claim 'Drug Z causes both A and B' → split into pharmacology-of-A and pharmacology-of-B sub-claims."
  ],
  "success_criteria": "Verdicts cite per-sub-claim evidence; reviewer can audit each piece independently.",
  "confidence": 0.7
}
```

Returns:

```json
{
  "review_id": "skr-abc123-7f8e9d0a1b",
  "state": "pending",
  "dedupe_action": "created",
  "pending_count": 1,
  "review_url": "/skills"
}
```

## Args

### Required for every proposal

| Field | Type | Notes |
|--|--|--|
| `name` | string slug (`a-z0-9._-`) | Stable identifier; reviewer can rename on approve |
| `description` | string, ≤2000 chars | What the skill does — 1-3 sentences |
| `rationale` | string, ≤2000 chars | **Why** the proposer wants this. Required, surfaced verbatim to the operator. Vague proposals get rejected fast. |
| `kind` | `api` / `behavior` / `knowledge` | Drives the cross-field validation rules below |

### Required for `kind=api`

| Field | Type | Notes |
|--|--|--|
| `service_name` | string | External service label (e.g. "GitHub Issues API") |
| `base_url` | string | Must start with `http://` or `https://` |

Optional `kind=api` fields: `auth_method`, `auth_secret_hint`, `required_hosts[]`, `required_secrets[]`, `endpoints[]`, `pricing_notes`, `rate_limit_notes`, `source_url`.

### Required for `kind=behavior`

| Field | Type | Notes |
|--|--|--|
| `examples` | list[string] (≥1) | Concrete usage examples (≤500 chars each, max 8). The reviewer needs them to judge the heuristic. |

Optional `kind=behavior` fields: `success_criteria`.

### Required for `kind=knowledge`

Just the base required fields — no extras. Use this for domain rules / facts the agent wants the runtime to surface on future runs via long-term memory retrieval.

### Always optional

`confidence` (float, 0..1, clamped, default 0.5) — sorts the review queue and seeds the bandit prior.

## Operator config

Edit on the **Plugins** page. Stored in `plugin_configs.propose_skill`.

| Field | Default | Meaning |
|--|--|--|
| `enabled` | `true` | Master switch. Set `false` to refuse all agent proposals globally. |
| `max_pending_per_agent` | `20` | Per-agent ceiling on pending proposals. Beyond this the plugin refuses with a clear error so the agent stops generating new ones until you drain the queue. |
| `cooldown_seconds` | `60` | Same agent + same `name` within this window is treated per `dedupe_strategy`. Set `0` to disable. |
| `dedupe_strategy` | `reject_duplicate` | `reject_duplicate` refuses an in-window dupe; `update_pending` overwrites the existing pending row's payload (handy when the agent is iterating on the same proposal). |
| `notify_on_proposal` | `true` | Fire a `HITL_SKILL_REVIEW` notification when a new proposal lands so the bell lights up. Off if you'd rather drain the queue on a schedule. |

## Failure modes

| Error | Cause | Fix |
|--|--|--|
| `propose_skill: operator has disabled skill proposals` | `enabled: false` | Flip it on, or just leave the agent unable to propose. |
| `propose_skill: agent X already has Y pending proposals (cap N)` | Queue full | Approve / reject the backlog on the Skills page. |
| `propose_skill: a pending proposal named X already exists` | Same name within `cooldown_seconds`, `reject_duplicate` strategy | Wait the cooldown, or switch to `update_pending`, or pick a new name. |
| `propose_skill: kind=api requires service_name AND base_url` | Missing api-flavor fields | Pass them, or use `kind=behavior` / `kind=knowledge` if the proposal isn't an external service. |
| `propose_skill: base_url X must start with http:// or https://` | Bogus URL | Use a real URL or use `kind=behavior`. |
| `propose_skill: kind=behavior requires at least one concrete example` | Empty `examples` for a behavior skill | Add 1-3 short examples (≤500 chars each). |

## Safety properties

- **Human gate is non-negotiable.** No code path activates a skill without operator approval; the plugin only inserts pending rows.
- **Rate-limited.** `max_pending_per_agent` keeps a runaway agent from filling the queue.
- **Audited.** Every successful insert (and every "updated existing") writes an audit-log row at `info` severity, actor `agent:{agent_name}`, kind `skill.proposed_by_agent` / `skill.proposal_updated`.
- **Notified.** A `HITL_SKILL_REVIEW` notification fires by default so you can't miss new proposals.
- **No network reach.** Empty `required_permissions` — even an evil agent can't pivot through this plugin.

## Further reading

- [Concepts: Skills](Concepts-Skills) — the mental model for the two-source review queue
- [Skill Catalog](Skill-Catalog) — the operator's review-page workflow
- [docs/learning-and-skills.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/learning-and-skills.md) — source-level reference
