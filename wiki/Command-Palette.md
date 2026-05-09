# Command Palette & Keyboard Shortcuts

The command palette is the fastest way to navigate EmberSpark's web UI. One chord opens it; fuzzy-search gets you anywhere.

## Opening

- **macOS**: `cmd + K`
- **Linux / Windows**: `ctrl + K`

A dark overlay appears with a search input. Type to filter by page name or description. `Enter` to navigate to the highlighted entry. `esc` to close.

`cmd/ctrl+K` is the **only** way to open the palette. It works from any page, including while a text input has focus — the chord is captured before the keystroke reaches the input.

## Other shortcuts

| Key | Action |
|---|---|
| `?` | Show keyboard shortcuts help overlay |
| `/` | Focus the page's search input (skipped while typing in a field) |
| `esc` | Close the open modal / palette |

`?` and `/` are single-key shortcuts and only fire when no text input has focus.

## Why there are no "goto" chord shortcuts

Earlier versions of EmberSpark exposed `g` + letter chords (e.g. `g c` for Chat, `g l` for Plugins). They were removed because they fired even while typing — any chat message or settings field containing "gl", "gc", "gp" etc. would teleport the operator mid-sentence. The ⌘K palette covers every navigation target and never interferes with typing.

## The full palette list

The command palette covers every page:

- Overview
- Provider
- Agents
- Chat
- Runs
- Persona
- Plugins
- Scheduler
- Templates
- Cost & Budgets
- Memory
- Downloads
- Skills
- Stats
- Security Center
- Secrets
- Guardrails
- Filtering
- Forensic
- Audit Log
- Ops
- Settings

Fuzzy matching is substring-based on both the page label and a short hint, so typing `audit` finds Audit Log, typing `scheduling` finds Scheduler, typing `guard` finds Guardrails.

## Further reading

- [Web UI Guide](Web-UI-Guide) — tour of each page
