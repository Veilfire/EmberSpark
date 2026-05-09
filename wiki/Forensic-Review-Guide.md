# Forensic Review Guide

The **Forensic** page in the web UI gives admins a way to rewind a
task run and see exactly what the agent saw, said, and did — every
prompt, model response, tool call, and memory touch — down to the
decrypted JSON payload per step.

Captures are **opt-in per run**, **per-run encrypted**, and
**TTL-expiring** (default 7 days). A run with forensic off pays no
overhead.

## When to use it

- You had a failed run and want to know *why* the planner picked
  the wrong tool.
- A plugin returned something surprising and you want to compare
  the raw vs. filtered result the model actually saw.
- You're building a new agent and want to step through the first
  few iterations to calibrate prompt + memory + playbook selection.
- You're investigating an incident and need the chain of thought
  as evidence.

Do **not** use forensic capture as your primary observability —
that's what `spark logs` and the Ops page are for. Forensic is
heavy: it encrypts and stores every payload, so save it for runs
where you genuinely need the full record.

## Enabling a capture

### From the CLI

```
spark task run --forensic "debugging email flow" tasks/email_flow.yaml --agent agents/inbox.yaml
```

The `--forensic` flag takes a required reason. That string lives on
the capture row forever and shows up in the viewer so future-you
knows what was being investigated.

### From task YAML

```yaml
spec:
  forensic:
    enabled: true
    ttl_hours: 168      # 7 days
    reason: "debugging email flow"
```

Commit this for a short investigation window, then revert.

## Viewing a capture

Navigate to **Forensic** in the sidebar (admin role required). The
list shows every active capture with its agent, task, reason, and
TTL.

Click **Inspect** to open the run detail view:

- **Header** — run_id, agent, task, captured timestamp, expiry,
  reason, and the **Wipe** button.
- **Iterations** — a chip strip that filters the chain below by
  iteration. Click **all** to see the full chain.
- **Chain** — the left pane, one button per snapshot. Color-coded
  by kind: blue=prompt, green=model, orange=tool, violet=memory,
  gray=reflection. Click to select.
- **Payload** — the right pane. Shows the full decrypted JSON for
  the selected snapshot with a **Copy JSON** button.

Every snapshot read is audited at `info` severity.

## Wiping a capture

Two paths:

1. **UI**: the red **Wipe** button on the run detail page.
2. **CLI**: `spark forensic wipe <run_id>` (prompts for confirm).

Wipe is **cryptographic shred**: it deletes the per-run age
identity from the secrets vault first, then drops the snapshot rows,
then marks the capture row `wiped_at`. Even if the second step
failed, the data is already permanently unreadable.

Expired captures are wiped automatically by a nightly retention
sweep — you only need to wipe manually if you want to drop
something early.

## What gets captured

| Kind | Captured |
|---|---|
| `prompt` | Assembled system prompt, user message, retrieved-memory context, playbook id, message count |
| `model` | Full response text, reasoning blocks (when the provider exposes them), requested tool calls, stop reason |
| `tool` | Plugin, post-merge args, raw plugin result, filtered model-visible result, redaction labels, error code if any |
| `memory_retrieved` / `memory_written` | Memory ids + records for reads and writes |
| `reflection` | Post-run summary, lessons, patterns, follow-ups |

The **raw** tool result is captured but encrypted. The **filtered**
field shows exactly what the model saw. Both are decryptable by any
admin with the per-run identity.

## Configuration

Relevant YAML:

```yaml
# task.yaml
spec:
  forensic:
    enabled: false       # off by default — always opt in
    ttl_hours: 168       # 7 days; min 1, max 2160 (90 days)
    reason: ""           # REQUIRED when enabled=true
```

Nightly retention sweep runs at `17 3 * * *` (03:17 local). You can
trigger it on demand via the API:

```
POST /api/forensic/retention/sweep
```

## CLI reference

```
spark forensic list                          # active captures
spark forensic show <run_id>                 # metadata for one capture
spark forensic wipe <run_id>                 # cryptographic shred (confirm)
spark task run --forensic "<reason>" task.yaml --agent agent.yaml
```

## Threat model

- **Admin compromise** = full forensic access. There is no
  separation of duties within the admin role.
- **Host compromise** with filesystem access to the age vault is
  equivalent to admin.
- **Backups** containing both the SQLite DB and the age vault can
  be decrypted offline. Treat backups with the same sensitivity as
  the live system.
- **Network-level exposure**: captures never leave the host. No
  remote telemetry, no cloud export.

## Related

- [docs/forensic.md](../docs/forensic.md) — source-level reference
- [Concepts: Privacy](Concepts-Privacy) — how privacy filtering
  shapes the raw vs. filtered result fields
- [Error Codes](Error-Codes) — the structured tool errors that
  show up in `ForensicToolSnapshot.error_code`
