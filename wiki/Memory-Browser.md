# Memory Browser

The **Memory** page in the web UI is a multi-tabbed browser over EmberSpark's long-term memory (Chroma + SQLite index), playbook stats, retention pruning, the operator review queue, the 2D embedding visualizer, and memory circles.

For the conceptual model, see [Concepts: Memory](Concepts-Memory) and [Concepts: Learning](Concepts-Learning).

## Tab — Long-term memory

Every row in the `long_term_memory_index` SQLite table, filtered by namespace if you set one.

| Column | What it is |
|---|---|
| `memory_id` | Stable primary key |
| `namespace` | Per-agent scope (or `__global__` / `__consensus__`) |
| `memory_type` | `fact` / `lesson` / `pattern` / `preference` / `constraint` / `result` |
| `sensitivity` | `low` / `moderate` / `high` / `restricted` |
| `retention_class` | `persistent` / `review` / `temporary` / `expiring` |
| `confidence` | 0–1 — decays with disuse, bumps on successful citation |
| `content_summary` | The distilled summary (the query target for vector retrieval) |
| `updated_at` | When the record last changed |

Rows also display **badges** for special states:

- **Anti-pattern** — the memory is a "don't do this" lesson, surfaced in retrieval under "Known failure modes to avoid".
- **Global** — the memory lives in the shared `__global__` pool, readable by any agent with `read_global: true`.
- **Consensus** — promoted to `__consensus__` because ≥2 agents independently arrived at the same conclusion.
- **Quarantined / Pending review** — automatic quarantine caught suspicious content (imperative side-effectful commands, permission-grant conflicts); not retrievable until an operator approves.
- **Contradicts** — contradiction detection linked this record to at least one other; click to see the pair.
- **Superseded** — consolidation replaced this memory with a crystallized summary; kept for provenance.
- **Expired** — `valid_until` has passed; deprioritized in retrieval.

### Operator actions

- **Filter** by namespace (type a value in the input, the list filters)
- **Add memory** (button, top-right) — operator creates a new record with summary / sensitivity / retention / tags. Defaults to `source_type: manual_note`; audited at `elevated`.
- **Edit** an existing row (inline pencil) — update summary, sensitivity, retention, tags. Audited.
- **Delete** a row — removes it from both SQLite index and the Chroma collection
- **Promote to global** — pushes a private memory into `__global__` if the agent's sharing config allows; gated by `max_cross_scope_sensitivity`
- **Click** a row — opens the detail drawer with full metadata, **provenance** graph (source run, source tool span, derived-from memories), and citation history

### What this lets you do

- Audit what the agent has learned
- Hand-author facts the operator knows the agent should have
- Spot bad lessons from failed reflections
- Prune stale memories that are cluttering retrieval
- Check namespace isolation (different agents have different namespaces)
- Trace "why did the agent say that?" via the provenance panel

## Tab — Review queue

Single inbox for every memory needing operator attention:

- `status != active` (quarantined / pending review)
- `contradicts_with` is non-empty
- `confidence < 0.3`
- Synthesis hypotheses waiting for confirmation

Each row has quick-actions: approve / dismiss / edit / promote-to-global / delete. Bulk actions too. This is the **one place** to handle memory hygiene instead of hunting through the main table.

## Tab — Visualize

2D UMAP-style scatter projection of the memory embedding space for the current namespace. Each point is one memory, colored by `memory_type`. Hover a point for the summary; click to open the detail drawer.

Use this to:

- Spot clusters of near-duplicates that consolidation might eventually crystallize
- See whether the agent's knowledge base is balanced or concentrated in one area
- Find outlier memories (far from every cluster) that might be worth reviewing

A companion **timeline** strip below the scatter shows memory creation over time, broken out by type lane.

## Tab — Circles

CRUD for memory circles (named shared pools).

- **Create a circle** with id + description
- **Add agents** to a circle with per-membership `can_read` / `can_write`
- The special `__global__` circle is always present and is the landing zone for the `read_global` / `write_global` sharing toggles on the agent detail page
- Every cross-scope read from a circle is audited at `elevated` severity

## Tab — Playbooks

Per-agent list of playbooks with their bandit state and running stats.

| Column | What it is |
|---|---|
| `name` | Derived from the objective fingerprint |
| `uses` | Total times this playbook has been selected |
| `success_rate` | `α / (α + β)` from the Beta posterior |
| `avg_duration_seconds` | EMA of wall-clock duration |
| `avg_tool_calls` | EMA of tool calls per successful use |
| `last_success_at` | Timestamp of most recent success |
| `tool_sequence` | The tool names (ordered, dedup'd) the playbook uses |

Delete a playbook that has a low success rate and more than a few uses, or that's obsolete because the task it addresses no longer exists, or whose tool sequence no longer matches the plugin allowlist. The bandit stops knowing about it; a fresh playbook will be derived by the next successful run on a similar objective.

## Tab — Pruning

Shows the current `memory_pruning` configuration, the next scheduled sweep, and the last run's counts.

Two buttons:

- **Run dry-run now** — computes the counts that *would* be pruned given the current `rollover_windows` without touching SQLite or Chroma. Writes an audit entry at `info` severity.
- **Run now** — admin-only. Runs the actual sweep. Deletes the matching rows from SQLite *and* the Chroma collection and fires a `MEMORY_PRUNED` notification (per Settings toggle).

### Configuration

`~/.spark/spark.yaml`:

```yaml
spec:
  memory_pruning:
    enabled: true
    schedule: "0 3 * * *"      # daily at 3am UTC
    rollover_windows:
      temporary: 7             # days — null = never prune
      expiring: 30
      review: 180
      persistent: null
    dry_run: false
    notify_on_prune: true
```

`rollover_windows` is per retention-class. A `null` value keeps that class forever regardless of age.

### CLI

```
spark memory prune              # run the sweep now
spark memory prune --dry-run    # count-only, no deletes
```

## Import / export

- **Export**: download namespace (or all) as JSONL; includes the canonical text, not just the summary. Admin-only; audited at `elevated`.
- **Import**: paste JSONL or upload. Conflicts resolved by canonical hash (skip-if-exists by default).

## Opening the memory database directly

If you need to query the raw SQLite:

```bash
sqlite3 ~/.spark/spark.db
```

Tables:

- `long_term_memory_index` — the index above
- `entity_memory` — extracted (subject, predicate, object) triples for structured lookups
- `memory_circles`, `circle_memberships` — circle ACL
- `playbooks` — bandit state
- `playbook_runs` — per-run playbook outcomes (immutable history)

Chroma is separate — its database is at `~/.spark/chroma/`. The BM25 sidecar index is a pickled corpus at `~/.spark/bm25/` (rebuilt automatically on upsert).

## Further reading

- [Concepts: Memory](Concepts-Memory) — the three-tier model, retrieval pipeline, lifecycle jobs
- [Concepts: Learning](Concepts-Learning) — how playbooks and reflection compose
- [docs/learning-and-skills.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/learning-and-skills.md) — source-level architecture
