# Concept: Memory

EmberSpark has **three tiers** of memory plus a **lifecycle** layer that ages, consolidates, and protects what's stored. This page covers the model, the pipeline, and the guarantees.

---

## Tier 1: Task memory

Ephemeral, in-process, scoped to a single run. Cleared on completion. Holds:

- Short-lived notes
- Temporary tool outputs that don't belong in longer-term storage
- Intermediate reasoning state
- Scratch context the engine uses while a run is in flight

You almost never need to think about task memory — it's the runtime's working set. When the run ends, it's gone.

---

## Tier 2: Session memory

Bounded-continuity memory across a sequence of related runs. Stored in SQLite. Used by:

- Recurring tasks with continuity
- Multi-step workflows
- Follow-up activity in the same logical thread of work
- Chat sessions (one `session_id` per conversation)

Session memory is **structured summaries**, not raw transcripts. Capped at `max_entries` per session with FIFO eviction.

Session memory is not retrieved into future *unrelated* runs. That's what tier 3 is for.

---

## Tier 3: Long-term memory

Persistent, namespace-isolated, vector-searchable. Backed by **Chroma** at `~/.spark/chroma/`. Each agent has its own namespace; cross-namespace reads default to refused and require explicit sharing configuration (see [Memory circles](#memory-circles-per-memory-acl) below).

A long-term memory record has:

| Field | Purpose |
|---|---|
| `memory_id` | Stable primary key |
| `namespace` | Per-agent scope |
| `content_summary` | Short distilled summary (the vector query target) |
| `canonical_text` | The full distilled form — still not a raw transcript |
| `memory_type` | `fact`, `lesson`, `pattern`, `preference`, `constraint`, `result` |
| `source_type` | `reflection`, `tool_result`, `user_input`, `manual_note`, `session_summary`, `consolidation`, `synthesis` |
| `sensitivity` | `low`, `moderate`, `high`, `restricted` — gates retrieval and exposure |
| `retention_class` | `persistent`, `review`, `temporary`, `expiring` — gates pruning |
| `confidence` | 0–1, fed into retrieval rerank; decays with disuse, recovers on successful citation |
| `usage_count`, `successful_citation_count` | Popularity counters — cited memories rank higher |
| `alpha`, `beta` | Beta posterior over citation outcomes (Bayesian confidence) |
| `is_anti_pattern` | When true, the memory is a "don't do this" lesson |
| `contradicts_with` | Memory ids that this one is known to conflict with |
| `superseded_by` | Set when this memory was replaced by a consolidation synthesis |
| `valid_from`, `valid_until` | Optional temporal validity window |
| `circle_id` | Null for private, `__global__` for shared pool, or a named circle id |
| `status` | `active`, `pending_review`, `quarantined` |
| `provenance_json` | Trace back to the tool call / run / source memory that birthed this record |
| `created_at`, `updated_at`, `tags`, `task_id`, `session_id` | Metadata |

**Long-term memory is never a raw transcript archive.** Every record is a distilled summary produced by reflection, synthesized offline, or explicitly added by the operator.

---

## Retrieval pipeline (hybrid + reranked)

When the engine (or chat handler) prepares context, it builds a compact **memory pack**:

1. **Query classification** — a cheap heuristic inspects the query for recency cues ("recent", "last"), factual cues ("what is", "when was"), etc., and selects a weight preset for the rerank step.
2. **Hybrid candidate generation** — pulls top-`N*3` by BM25 (sidecar index) **and** top-`N*3` by semantic similarity (Chroma). Both lists are unioned.
3. **Reciprocal Rank Fusion (RRF, k=60)** — the classic fused-rank score that beats either single-source list on every published benchmark.
4. **Cross-encoder rerank** — `cross-encoder/ms-marco-MiniLM-L-6-v2` (~22 MB, CPU-fast) re-scores the survivors.
5. **Metadata filter** — namespace, sensitivity ceiling, memory type, `valid_until < now`, quarantined/pending_review excluded.
6. **Rank fusion** — `rerank_score + confidence_weight*confidence + recency_weight*recency + popularity*log1p(successful_citation_count)`.
7. **Post-retrieval dedup** — collapses clusters of near-duplicates (cosine > 0.92) into one representative; the cluster size is surfaced as metadata so the model knows "3 similar memories confirm this".
8. **Top-k truncation** to the configured budget.
9. **Privacy pass** — every retrieved summary is re-filtered through `filter_for_model` before it reaches the prompt.
10. **Format** into a bounded "Known context" section in the system prompt, with **anti-patterns** surfaced separately as "Known failure modes to avoid".

Retrieval never dumps all results into the prompt — the pack is truncated and tagged.

---

## Write pipeline

Memory candidates come from five sources:

1. **Reflection** — after a run, the reflector proposes `memory_candidates` in its structured output.
2. **Skill approval** — when an operator approves a discovered skill, a distilled record lands as a `pattern` type memory.
3. **Operator manual** — CRUD from the Memory Browser UI (`source_type: manual_note`).
4. **Consolidation** — nightly clustering synthesizes groups of related memories into one `pattern`/`consolidation` record and marks sources `superseded_by`.
5. **Synthesis ("dreams")** — offline hypothesis generation over recent memories, lands as low-confidence `pattern` records pending confirmation by a real run.

Every candidate flows through `promotion.promote`:

1. **Classify** — memory_type, source_type, sensitivity, retention_class, confidence
2. **Redact** — regex + entropy + Presidio (same pipeline as logs)
3. **Contradiction check** — targeted retrieval against the incoming summary. If top hits are semantically close (cosine > 0.85), a small chat-model prompt decides `contradicts: bool`. On conflict, both memories get `contradicts_with` populated and an operator notification fires for review.
4. **Adversarial quarantine** — a classifier inspects the candidate for imperative commands with elevated side effects or content that contradicts permission grants. Suspicious candidates land as `status: pending_review` and are not retrievable until the operator approves.
5. **Normalize** into canonical summary text
6. **Hash dedup** against existing records in the same namespace
7. **Generate embedding** + BM25 tokens
8. **Write** to Chroma + SQLite `long_term_memory_index` + BM25 sidecar
9. **Populate provenance** — run_id, source memory ids, source tool spans

No raw content from a tool output ever reaches long-term memory unfiltered.

---

## Lifecycle jobs

EmberSpark runs four nightly/weekly jobs to keep long-term memory healthy:

| Job | Schedule | What it does |
|---|---|---|
| **Decay** | nightly 02:13 UTC | Reduces `confidence *= 0.98` for memories not retrieved in 7 days. Cited memories bump `confidence += 0.05` (capped at 1.0) on successful runs. |
| **Consolidation** | weekly Sun 03:30 UTC | HDBSCAN-lite clusters memories; clusters of ≥5 with avg age >14d are synthesized into one "crystallized" memory. Sources marked `superseded_by`, retention downgraded. |
| **Consensus detection** | weekly Sun 04:00 UTC | When ≥2 agents have effectively the same memory (canonical-hash or semantic similarity > 0.92), promotes it to the `__consensus__` namespace with a confidence bump. Consensus memories are read-only to all agents. |
| **Synthesis ("dreams")** | nightly 04:30 UTC | Per-agent hypothesis generation: samples recent memories, prompts for patterns / hypotheses / constraint candidates, writes low-confidence `pattern` records. |

Plus the scheduled **retention pruning** job that evicts expired memories by `retention_class` + age window (see [Memory Browser](Memory-Browser)).

---

## Memory circles (per-memory ACL)

By default every memory is **private** to its agent's namespace. Operators can widen access:

- **`read_global` / `write_global` on the agent's `MemorySharingConfig`** — participation in the special `__global__` pool. Bounded by `max_cross_scope_sensitivity`; promotions are admin-audited.
- **Named circles** — create a `MemoryCircle`, add agents with per-circle `can_read` / `can_write`. A memory's `circle_id` is null (private), `__global__` (shared pool), or a specific circle id. Retrieval pulls from `read_circles: list[str]` plus the agent's own namespace.

Every cross-scope read is audited at `elevated` severity.

---

## Provenance

Every memory record can carry `provenance_json`:

```json
{
  "source": "reflection",
  "run_id": "run-abc",
  "derived_from_memory_ids": ["mem-x", "mem-y"],
  "derived_from_tool_calls": [{"plugin": "http_tool", "span_id": 42}]
}
```

The Memory Browser surfaces this as a provenance sub-panel — click a derived-from id to jump to that memory or that span. Useful when the agent says something surprising and you want to trace "where did this come from?"

---

## Citation tracking

When a memory is retrieved into a turn and the resulting run succeeds, its `successful_citation_count` increments and `alpha` (Beta posterior) ticks up. Popular cited memories rank higher on subsequent retrievals. Unhelpful ones (retrieved but run failed) increment `beta`, pushing them down.

In chat, the UI emits a `citations` WebSocket frame per assistant turn and renders the memory ids as a collapsible footer below the message.

---

## The guarantee

Across all tiers, memory is **distilled**. EmberSpark never stores raw prompts, raw model outputs, or raw tool outputs in memory. Everything is summarized first, redacted second, written third, and reviewed in place — contradictions surface as notifications, quarantines gate suspect content, consolidation and decay prevent unbounded growth, provenance lets you audit "why".

If that's too aggressive for your use case, you can raise the sensitivity ceiling, widen the retention class, or flip `raw_prompts: true` in your agent YAML (audited at `critical` severity, strongly discouraged).

---

## Further reading

- [Memory Browser](Memory-Browser) — the UI for inspecting, creating, editing, and reviewing memory
- [Concepts: Continuous Learning](Concepts-Learning) — how reflection promotes memories, how playbooks compose
- [docs/learning-and-skills.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/learning-and-skills.md) — source-level architecture
