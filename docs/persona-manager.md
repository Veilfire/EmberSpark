# Persona Manager & Hot Reload

The persona manager is how you edit the system prompt, tone, and voice of your agent **live**, without restarting anything. This page covers the model, the workflow, and the guarantees.

---

## What a persona is

A persona is a named record that includes:

| Field | What it is |
|---|---|
| `name` | Short human label (e.g. `Concise`, `Research Analyst`, `Late-Night Debugger`). |
| `description` | One-line purpose summary, shown in the UI list. |
| `system_prompt` | The actual text prepended to every model call. This is the main thing. |
| `tone` | Optional free-text hint (e.g. "terse", "formal", "exploratory"). Appended after the system prompt. |
| `tags` | Optional labels for organization. |
| `is_active` | Exactly one persona is active at any time. |

Personas live in the `agent_personas` SQLite table. A starter persona (`pers-default`) is seeded on first boot.

---

## Hot reload: what actually happens

The engine's `_system_prompt` coroutine (in [`spark/runtime/engine.py`](../spark/runtime/engine.py)) is **async** and reads the active persona from the DB on **every** call to `_invoke_model`.

Concretely, inside the LangGraph loop:

```python
while True:
    self.budget.tick_iter()
    self.budget.tick_model()
    # Hot-reload: rebuild messages[0] (the system message) fresh every iteration.
    messages[0] = {
        "role": "system",
        "content": await self._system_prompt(state),
    }
    response = await self._invoke_model(model, messages)
    ...
```

`_system_prompt` calls `PersonaRepository.get_active()`, which is an indexed DB lookup on a boolean column — sub-millisecond.

### Practical consequences

- **You edit the persona in the UI and click Save & Activate.**
- **The next model call inside any running task reflects the change.**
- No restart. No process kill. No cache invalidation. No "please wait for the current task to finish."
- A chat session in mid-conversation picks up the new persona on the next user turn.
- A one-shot task in mid-run picks up the new persona on the next planner iteration.
- A recurring task picks up the new persona on the next fire (and also on the next iteration within a fire that hasn't finished yet).

### What hot reload is *not*

- It is not a "mid-flight interrupt." The model call currently in progress uses whatever system prompt was composed before it started. Hot reload kicks in on the **next** call.
- It is not retroactive. A previously-completed run's logs and memories already captured the old persona. There's no rewrite.
- It does not reload plugin code, memory, or any other subsystem. It only affects the system prompt composition.

---

## Editing a persona (Web UI)

1. Go to **Persona** in the sidebar (or `cmd+K` → "Persona", or press `g p`).
2. The left pane shows the list of personas, with the active one tagged `active`.
3. Click **New persona** to start fresh, or click an existing persona to edit it.
4. The editor shows:
   - **Name** — free-text label
   - **Tone** — optional short hint
   - **Description** — one-line summary
   - **Tags** — comma-separated labels
   - **System prompt** — the big textarea, monospaced, full-size
5. Click **Preview** at any time to see exactly what the model would receive as its system message for this persona. The preview includes the tone line and the boilerplate guards the engine appends.
6. When you're happy:
   - **Save** — updates the persona but does not change which is active.
   - **Save & Activate** — saves and sets this persona active. An `elevated`-severity `persona.activated` audit entry is written.

---

## Editing from the CLI or API

If you're comfortable with curl or scripts, you can drive the same flow through `/api/persona/*`. A typical sequence:

```bash
# List (cookie auth through the session, or X-Spark-Token header)
curl -H "X-Spark-Token: $(cat ~/.spark/web-token)" \
  http://127.0.0.1:7777/api/persona/

# Create a new persona
curl -X POST \
  -H "X-Spark-Token: $(cat ~/.spark/web-token)" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Concise",
    "description": "Short, direct, no fluff.",
    "system_prompt": "Respond tersely. One sentence when possible.",
    "tone": "terse",
    "tags": ["writing"]
  }' \
  http://127.0.0.1:7777/api/persona/

# Activate it (use the persona_id from the create response)
curl -X POST \
  -H "X-Spark-Token: $(cat ~/.spark/web-token)" \
  http://127.0.0.1:7777/api/persona/pers-abc123/activate

# Preview what the model will see
curl -X POST \
  -H "X-Spark-Token: $(cat ~/.spark/web-token)" \
  -H "Content-Type: application/json" \
  -d '{"objective":"summarize"}' \
  http://127.0.0.1:7777/api/persona/pers-abc123/preview
```

The same audit guarantees apply — every mutation hits the audit log, every activation is `elevated`.

---

## Iteration workflow

Persona hot reload was built for a specific workflow: **rapid refinement**.

You run the agent on a task. The output is too long / too terse / too formal / too sycophantic. You open the Persona page, adjust one sentence in the system prompt, click Save & Activate, kick the task again (or send the next chat message). You see the new behavior immediately. You keep tightening until the output matches your taste.

This is the same loop that's painful in most agent frameworks because you have to restart the process to change the system prompt. In EmberSpark, it's a one-click operation that takes effect on the next model call.

### Tips

- **Keep a "last known good" persona.** Before iterating on a persona, duplicate it first (create a new persona with the same content). If your edits go sideways, you can Activate the backup in one click.
- **Use tags.** EmberSpark doesn't have a "git log" for personas, but you can label them `v1`, `v2`, `v3` or `experiment-terse`, `production-formal` to keep track.
- **Preview before you activate.** The preview button shows you the full assembled system message the model will actually see, including tone and boilerplate. It's the fastest way to catch "oh wait that renders weird" issues before they hit the model.

---

## Safety guarantees

The repository enforces a small number of invariants that you can rely on:

1. **Exactly one active persona at any time.** `activate(persona_id)` is atomic: it flips all other `is_active` rows off and the target row on in a single transaction.
2. **The active persona cannot be deleted.** `delete(persona_id)` raises `ValueError` if that persona is active. Activate another persona first.
3. **Hot reload is per-iteration, not per-task.** A persona change mid-task is always at an iteration boundary — there's no way to slice into the middle of a model call.
4. **Every activation is audited.** The audit log shows who activated which persona and when, at `elevated` severity so it appears in the Guardrails page.
5. **The seeded default always exists.** If you somehow end up with no personas, the next boot seeds `pers-default` again.

---

## What about per-task personas?

EmberSpark is a single-agent runtime, and by design there's **one** active persona at a time. The rationale: you always know what the agent's voice is without having to look at task metadata.

If you want task-specific behavior, don't override the persona. Instead, put the task-specific framing in the task's `objective` field and let the persona provide the consistent voice. Good personas describe *how* the agent speaks; good objectives describe *what* this particular run is about.

---

## Troubleshooting

**The model doesn't seem to pick up my change.**

- Open the Audit Log page and look for a recent `persona.activated` entry. If it's there, the DB has the change. Check the timestamp.
- Go to the Persona page and verify the active badge is on the persona you expect.
- Send a fresh chat message. The change kicks in on the next model call, not the next token. If you sent a message before you activated, the response uses the old persona.

**I activated by accident.**

- Activate a different persona. That's it — deactivation by toggling doesn't exist (and shouldn't: we never want to be in a "no active persona" state).

**I want to roll back to the factory default.**

- The seeded default persona is `pers-default`. If you edited and saved over it, there's no built-in rollback — use the preview button and your browser history to reconstruct, or re-seed by deleting the row and restarting (`sqlite3 ~/.spark/spark.db "DELETE FROM agent_personas WHERE persona_id='pers-default';"`, then restart `spark serve`).

**I deleted the active persona somehow.**

- You can't — the repository refuses. If you're seeing an error about "active persona", you're doing it right and the invariant is protecting you.
