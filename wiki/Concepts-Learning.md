# Concept: Continuous Learning

EmberSpark's agent improves over time through three stacked mechanisms. They run in parallel and each fills a different gap.

---

## Layer A — Reflective learning

After every successful run, the reflector pass runs. It asks the model to summarize what happened using a structured `ReflectionRecord`:

```python
class ReflectionRecord(BaseModel):
    success: bool
    summary: str
    failures: list[str]
    lessons: list[str]
    memory_candidates: list[MemoryCandidatePayload]
    follow_ups: list[str]
```

Memory candidates flow through `spark.memory.promotion.promote` (redaction → dedup → embedding → Chroma) so they land in long-term memory. On a future run with a similar objective, they're retrieved into context and the model can see them.

This is the slowest, most general form of learning. It's "the agent remembers what worked."

---

## Layer B — Strategic: Playbooks + Thompson bandit

A **playbook** is a named procedure the agent has successfully used before. Playbooks live in SQLite (not Chroma) because their state — success rate, uses, avg tool calls — updates on every run.

Each playbook has a `Beta(α, β)` posterior over its success probability:

- α starts at 1, increments on success
- β starts at 1, increments on failure

When the engine plans a new run, it calls `PlaybookStore.find_applicable` with the current objective and available tools. For every applicable playbook it draws a Thompson sample from `Beta(α, β)`, blends with an applicability score, picks the argmax. With probability ε (default 0.05) it ignores the bandit and picks a random playbook to force exploration.

The selected playbook is rendered into the system prompt as a `RECOMMENDED PLAYBOOK` section:

```
RECOMMENDED PLAYBOOK (selected by bandit):
- name: summarize-repo
- success_rate: 0.87 over 23 uses
- tool sequence: http_client, markdown_writer
- description: Fetch metadata and write a markdown note.
Consider following this sequence unless new context argues otherwise.
```

The model is free to override, but has a strong hint. After the run, `PlaybookStore.record_outcome` updates the posterior and the EMA stats.

If no playbook was selected and the run succeeded with tools, `derive_playbook_candidate` produces a new playbook from the objective + tool sequence and stores it. That's how the library grows.

This is faster and more explicit than pure reflection — "the agent gets better at things it has done before."

---

## Layer C — Skill acquisition

See [Concepts: Skills](Concepts-Skills). Skills are knowledge about external APIs that the agent can learn by fetching official docs, structured by the model with `with_structured_output`, and staged for human review.

This is "the agent learns how to use things it has never used before."

---

## How the layers compose

- **A (reflection)** catches general lessons and stores them as memories. Slow, broad.
- **B (playbooks)** specializes on repeated patterns and selects known-good sequences. Fast, narrow.
- **C (skills)** extends the agent's *capabilities* by acquiring external API knowledge. Different axis entirely.

All three feed into the same prompt composition step. The `_system_prompt` function in the engine layers them:

```
PERSONA SYSTEM PROMPT
----
RECOMMENDED PLAYBOOK (from bandit selection)
----
RETRIEVED MEMORIES + SKILLS (filtered, counted, bounded)
----
TOOL CALL INSTRUCTIONS
```

---

## What prevents runaway learning

Learning is bounded just like everything else:

- **Reflection only runs on success.** Failed runs don't promote memories.
- **Memory promotion is redacted + deduplicated + sensitivity-gated.** A single repeated run doesn't fill the store with duplicates.
- **Contradiction detection fires at promote time.** Incoming memories that conflict with existing ones raise a notification for operator review instead of silently landing.
- **Adversarial quarantine.** Candidates that look like injected commands or that conflict with permission grants are held as `pending_review` and are not retrievable until approved.
- **Playbooks only reinforce on success.** A playbook that fails doesn't get α incremented.
- **Memory retrieval is sensitivity-gated.** Strict mode refuses to retrieve `high` or `restricted` records for model exposure.
- **Skills require human review.** No agent-discovered skill is ever auto-activated.
- **Retention classes age memories out.** `temporary` memories expire after 7 days, `expiring` after 30, `review` after 180, `persistent` never.

---

## Offline lifecycle jobs

Four scheduled jobs keep the learning substrate healthy without operator intervention:

- **Decay** (nightly 02:13 UTC) — untouched memories lose confidence slowly; cited ones regain it.
- **Consolidation** (weekly Sun 03:30 UTC) — clusters of ≥5 related memories get crystallized into one synthesis record; sources marked `superseded_by`.
- **Consensus detection** (weekly Sun 04:00 UTC) — when multiple agents independently learn the same thing, it gets promoted to a shared `__consensus__` pool.
- **Synthesis** (nightly 04:30 UTC) — opt-in "dream" pass that produces low-confidence hypotheses from recent memories, which must be confirmed by a real run to graduate.

See [Concepts: Memory](Concepts-Memory) for the full lifecycle picture.

---

## Further reading

- [docs/learning-and-skills.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/learning-and-skills.md) — full architecture reference
- [Concepts: Memory](Concepts-Memory) — the storage layer learning writes to
- [Concepts: Skills](Concepts-Skills) — the skill acquisition loop
- [Memory Browser](Memory-Browser) — inspect what the agent has learned
