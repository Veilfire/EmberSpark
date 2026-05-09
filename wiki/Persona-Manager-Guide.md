# Persona Manager Guide

The Persona Manager lets you refine your agent's system prompt live, without restarting anything. Edit, save, activate, run — the next model call uses the new persona.

For the conceptual background, see [Concepts: Personas](Concepts-Personas). For source-level detail, see [docs/persona-manager.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/persona-manager.md).

---

## Quick start

1. Open **Persona** in the sidebar (or `cmd+K` → Persona).
2. The left pane shows the list of personas with the active one tagged.
3. Click an existing persona to edit, or **New persona** to start fresh.
4. Edit `system_prompt`, `tone`, `description`, `tags`.
5. Click **Preview** to see exactly what the model will receive.
6. Click **Save & Activate**.
7. The next model call in any running task uses the new persona.

No restart. No cache to invalidate. The runtime reads the active persona from the DB on every iteration.

---

## The persona fields

| Field | What it is | Example |
|---|---|---|
| **Name** | Short human label shown in the list | `Concise`, `Research Analyst`, `Late-Night Debugger` |
| **Description** | One-line summary shown in the list | "Short, direct, no fluff." |
| **System prompt** | The actual text prepended to every model call | The big textarea at the bottom of the editor |
| **Tone** | Optional free-text hint appended after the system prompt | "terse, operator-focused" |
| **Tags** | Optional labels for organization | `v2, experiment, production` |

---

## The edit → activate loop

Persona hot reload is designed for a specific workflow: **rapid iteration**. You run a task, see the output, decide it's too verbose / too formal / missing something, open the persona, tweak a sentence, click Save & Activate, re-run (or send the next chat message). You see the new behavior immediately.

This is the loop you actually want when tuning an agent's voice, and it's painful in most other frameworks because they cache the system prompt at process start.

### Typical iteration cadence

- 5–10 minutes per revision
- Keep changes small — one sentence at a time
- Use the Preview button before activating to catch obvious issues
- Run the same test task after each revision so you can compare outcomes
- Duplicate the persona before large rewrites so you have a "last known good"

---

## When the change takes effect

The DB read happens **inside the LangGraph loop**, on every model call. So:

- **One-shot task** — the next planner iteration picks up the change
- **Recurring task** — the next fire picks up the change (and the next iteration within that fire)
- **Chat session** — the next user turn picks up the change
- **Mid-flight model call** — **not** affected. The in-progress call uses whatever system prompt was composed before it started. Changes kick in on the *next* call.

If you want to force an immediate effect on a running task, you have to stop and restart the task. But usually you don't need to — iteration is per-message in chat and per-iteration in task runs.

---

## Preview

The **Preview** button calls `POST /api/persona/{id}/preview` which returns the assembled system message the engine would compose for this persona. This is the text the model will actually see:

```
[system_prompt body]
Tone: [tone text]
You operate under strict budgets and a plugin allowlist.
Privacy mode: strict.
Respond with a JSON tool call object `{"tool": "name", "args": {...}}` when
you need to invoke a plugin, otherwise respond with the final answer.
```

The preview only shows the persona-specific part. During a real run, the engine also appends `RETRIEVED MEMORIES + SKILLS` and `RECOMMENDED PLAYBOOK` sections, but those are run-specific.

Use preview to:

- Catch typos before they hit the model
- Verify markdown formatting in the system prompt
- Confirm the tone line reads naturally
- Spot accidental template variables

---

## Multiple personas

You can have as many personas as you want. Only one is active. Use this to:

- **Keep known-good versions** — duplicate the current active before iterating, activate the copy, experiment, flip back if things go sideways
- **A/B test** — prepare two personas with different framings, run the same task against each (activate, run, check output, switch, run again)
- **Task-specific voices** — a research persona vs. a debug persona vs. a chat persona. Switch between them as the context demands.

### How duplicating works

Click **New persona**, then manually copy-paste the content from the current active persona. EmberSpark doesn't have a "fork" button (yet) — but the workflow is fast enough that it's not a bottleneck.

---

## Safety guarantees

The Persona Manager enforces a few invariants:

1. **Exactly one persona is active at a time.** Activating a new one flips the old one off in the same transaction. No "no active persona" state is reachable.
2. **The active persona cannot be deleted.** The UI hides the Delete button when looking at the active persona. The API returns 409 Conflict if you try.
3. **Every activation is audited** at `elevated` severity. The Audit Log shows who activated which persona and when.
4. **The seeded default always exists.** On first boot, EmberSpark creates `pers-default` if no personas exist. If you accidentally end up with zero personas, restart the UI and the default is re-seeded.

---

## Common operator questions

### "I changed the persona but the model is still using the old voice."

Check:

1. **Audit Log** — is there a recent `persona.activated` entry? If not, the DB doesn't have your change (maybe you forgot to click Save & Activate).
2. **Persona page** — is the active badge on the persona you think?
3. **Timing** — did you send a new chat message *after* activating? The change applies on the next model call, not retroactively.

If all three check out and the change still doesn't land, there might be a chat session caching messages from before the activation. Start a new chat session.

### "I want to roll back to a previous version."

EmberSpark doesn't have built-in persona versioning. The workaround:

- Before iterating, duplicate the persona (copy-paste its content into a new one).
- Keep the old one around with a tag like `v1-backup`.
- If you want to roll back, activate the backup.

If you want real versioning, an easy way is to use git — export persona JSON via the API, commit it. Each commit is a version.

### "I want different personas for different tasks."

EmberSpark is a single-agent runtime with a single active persona. By design. If you want task-specific behavior, put the task-specific framing in the task's `objective` and let the persona provide the consistent voice. Good personas describe *how* the agent speaks; good objectives describe *what* this run is about.

If you absolutely need two voices at once, you need two separate `spark serve` instances with different `~/.spark/spark.yaml` and different state directories. That's a bigger change.

### "Can I edit the persona while a long-running task is in flight?"

Yes. The next iteration of the task picks up the new persona. The currently-in-flight model call finishes with the old persona, then the next plan step uses the new one.

### "Is there a way to edit via the CLI?"

Yes — the `/api/persona/*` endpoints work with curl and the headless token. See [API Reference](API-Reference) for details. Any change through the API is audited the same way as UI changes.

---

## Further reading

- [Concepts: Personas](Concepts-Personas)
- [docs/persona-manager.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/persona-manager.md) — source-level deep dive
- [Web UI Guide](Web-UI-Guide) — the full UI tour
