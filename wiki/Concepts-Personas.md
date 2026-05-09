# Concept: Personas

A persona is a named record that holds the system prompt, tone, and voice of your agent. EmberSpark is a **single-agent runtime**, so you have one agent — but you can have many personas and hot-swap between them.

## Why personas are their own thing

The system prompt is the single most-iterated-on piece of any agent. You tweak it, see what the model does, tweak again. In most frameworks, this loop is slow because you have to restart the process to change the prompt.

EmberSpark decouples the prompt from the process. The engine re-reads the active persona from the DB on **every** model call. Edit the persona in the web UI, click Save & Activate, and the next model call inside any running task picks up the new prompt. No restart.

## What a persona contains

| Field | Purpose |
|---|---|
| `name` | Human label (e.g. "Concise", "Research Analyst") |
| `description` | One-line summary for the UI list |
| `system_prompt` | The actual text prepended to every model call |
| `tone` | Optional free-text hint (appended after the system prompt) |
| `tags` | Optional labels for organization |
| `is_active` | Exactly one persona is active at a time |

## Hot reload semantics

1. Persona changes take effect on the **next model call**, not the next task.
2. A run already in flight picks up the new persona on its next iteration.
3. A chat session picks up the new persona on the next user turn.
4. The scheduler picks up the new persona on the next fire.

The active persona cannot be deleted — you have to activate another first, then delete. One persona is always active; the runtime refuses to be in a "no persona" state.

Every activation writes an `elevated`-severity audit entry.

See the [Persona Manager Guide](Persona-Manager-Guide) for the operator-level how-to, and [docs/persona-manager.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/persona-manager.md) for the source-level reference.
