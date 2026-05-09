# Continuous Learning & Skills

This document covers how EmberSpark gets better over time — both at things it already does and things it has never done before.

## Three layers

EmberSpark runs three learning layers in parallel:

1. **Reflective (A)** — After every successful run the existing reflection pass now also derives a `PlaybookCandidate`: a name, a description, and the distinct tool sequence the run used. The candidate is promoted through `PlaybookStore.upsert_from_candidate`, which de-duplicates by a stable `fingerprint(objective_normalized, tool_set)`.
2. **Strategic (B)** — A Thompson-sampling bandit selects among applicable playbooks for a new run. Each playbook tracks a `Beta(α, β)` posterior over its success probability. On each outcome:
   - `α ← α + 1` on success,
   - `β ← β + 1` on failure.
3. **Skill (C)** — When the agent encounters a capability gap (an unknown API), it runs the `SkillDiscovery` subgraph to fetch official docs from a trusted source, extract a structured `ApiSkill`, and stage it for human review. Approved skills become retrievable context during future planning.

## Data model

- `playbooks` — SQLite table. One row per named procedure; stats update in place. `alpha`/`beta` carry the bandit state; `uses`, `avg_duration_seconds`, `avg_tool_calls`, `avg_model_calls` track running aggregates via EMA.
- `playbook_runs` — immutable per-run observations for offline analysis.
- `skills` — SQLite table of approved skills.
- `skill_reviews` — pending queue with full `ApiSkill` JSON payload.
- Long-term memory (Chroma) — approved skills are also promoted into long-term memory as `PATTERN` type records for similarity retrieval.

## Engine integration

`RuntimeEngine._retrieve_memory_context`:

1. Calls `PlaybookStore.select_for_run` — the bandit picks a playbook if any apply.
2. Loads up to 5 approved skills for the agent from SQLite and injects them as `[SKILL]`-tagged memories.
3. Runs the existing Chroma retrieval.

The selected playbook's name, success rate, and tool sequence are rendered into the system prompt as `RECOMMENDED PLAYBOOK`. The model is free to override, but has a strong hint.

`RuntimeEngine._update_learning` (post-run):

1. If a playbook was selected, `record_outcome` updates its Beta posterior + EMA stats.
2. If no playbook was selected but the run used tools successfully, a new playbook is derived from the run's objective + tool sequence and stored with an initial successful outcome.

## Skill discovery flow

```
capability gap
      │
      ▼
 SkillDiscovery._plan         ← with_structured_output(_DiscoveryPlan)
      │                         (service_name, doc_host, doc_url)
      ▼
 TrustedDocPolicy.allows?     ← fails closed if host not trusted
      │
      ▼
 http_call(doc_url)            ← uses existing http_client SSRF defense
      │
      ▼
 SkillDiscovery._extract      ← with_structured_output(ApiSkill)
      │
      ▼
 SkillCatalog.stage_for_review ← row in skill_reviews, state=pending
      │
      ▼
 human review (CLI or UI)     ← `spark skills review` / SkillCatalog page
      │
      ▼
 approve → skills + long-term memory
 reject  → stays in queue with reviewer notes
```

**Safety properties:**

- Skill discovery is only permitted to fetch from `TrustedDocPolicy` — a *second* allowlist distinct from the agent's general network grants. Operators manage it in the Security Center → Trusted Docs panel.
- No skill is ever auto-approved. Every new skill requires `SkillReviewDecision` from a human reviewer.
- Using a skill still requires the *target* API host to be in the agent's normal `network.allow_hosts`. If it isn't, the agent halts with a clear message so the operator can add it (or not).
- The AI labels the skill (name + description) during extraction. The reviewer can edit both in the UI before approval.

## Operator workflow

CLI:

```bash
spark skills review                       # list pending
spark skills approve skr-abc123 \
  --reviewer jes --notes "looks clean"
spark skills reject skr-def456 \
  --reviewer jes --notes "out of scope"
```

Web UI: **Skills** → pending cards with name/description editors → Approve / Reject.

## Observability

- `memory.promoted` event fires with `memory_id` and `duplicate` flag.
- `playbook.selected` log event carries `playbook_id`, `name`, `success_rate`, `uses` at selection time.
- `skill.staged` / `skill.approved` / `skill.rejected` audit entries (elevated severity on approval).
- The Memory browser in the UI shows per-agent playbook stats and the full long-term memory index; the Skills page shows pending reviews and approved skills.
