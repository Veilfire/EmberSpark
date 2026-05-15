# Plugin: `imap_reader`

Read inbound email over IMAP. Pairs with the existing `email_sender`
so an agent has the full inbox loop. No new dependencies — uses
stdlib `imaplib` + `email`.

| | |
|---|---|
| **Required permissions** | `NET_HTTP`, `SECRETS_READ` |
| **Sensitivity** | `HIGH` (email bodies are PII-dense; strict-mode agents won't see content; balanced will) |
| **Network** | Yes — IMAP over SSL on 993 |
| **Output filtered** | Yes — bodies pass through the redaction chain (PANs / SSNs / etc. scrubbed) |

## Actions

| Action | What it calls | Required config |
|---|---|---|
| `list_mailboxes` | IMAP LIST | none |
| `search` | IMAP SELECT (readonly) + UID SEARCH + UID FETCH (envelope only) | `mailbox` in `allowed_mailboxes` |
| `get_message` | IMAP SELECT + UID FETCH (RFC822) | `mailbox` in `allowed_mailboxes`; `uid` |

Search criteria the model can pass: `since`, `before`, `from_address`, `to_address`, `subject`, `body`, `unseen`, `flagged`.

## Bootstrap

1. **App password** — generate from Google App Passwords, Microsoft App Passwords, FastMail "App Passwords", etc. **Never the login password**.
2. **Store in vault** — `spark secrets set imap_password`.
3. **Plugin config** — `/plugins` → `imap_reader`. Fill host (e.g. `imap.gmail.com`), port (default 993), username, secret name. Click **Test connection & discover**. The plugin lists every mailbox the account exposes; tick the ones the agent should read. `[Gmail]/All Mail`, `[Gmail]/Trash`, `[Gmail]/Spam` carry a **danger** chip and require a typed-confirm before activation.

## Risk classification

The editor surfaces these chips automatically:

- **safe**: `INBOX`, custom folders (most user-created)
- **elevated**: `Sent`, `Drafts`, `Trash`, `Junk`, `Archive`, `[Gmail]/Sent Mail`, `[Gmail]/Drafts`, `[Gmail]/Important`, `[Gmail]/Starred`
- **danger**: `[Gmail]/All Mail`, `[Gmail]/Trash`, `[Gmail]/Spam`

## Failure surface

| Refusal | Code | Inspector deep-link |
|---|---|---|
| 401 from server | `SPK_E_SECRET_NOT_FOUND` | `/secrets` |
| Mailbox not in `allowed_mailboxes` | `SPK_E_PERMISSION_MISSING` | `/plugins?prefill=…` — editor opens with the matching mailbox flashed + ticked; danger mailboxes auto-trigger the typed-confirm modal |
| Connect refused on RFC1918 | `SPK_E_URL_PRIVATE_IP` | `/security?tab=network` |
| Host not in agent's `allow_hosts` | `SPK_E_URL_DENIED` | Same |

## Safety design

- **Read-only IMAP SELECT** by default (`mark_seen_on_read=false`) — the plugin doesn't mutate server state unless the operator opts in.
- **Per-message body cap** (`max_body_bytes`, default 256 KB) — one ginormous email can't blow the prompt window.
- **Attachments off** by default — even allowed mailboxes don't surface attachments unless `download_attachments=true`. When on, attachments route to `deliverables_path` (Downloads page) rather than streaming into the prompt.
- **Sensitivity HIGH** — bodies pass through Presidio for `EMAIL_ADDRESS` / `PHONE_NUMBER` / `LOCATION` / `PERSON` / card / SSN redaction before reaching the model.

## Recipes

### "Did Linda reply about the contract?"

`search` with `from_address="linda@example.com"`, `subject="contract"`, `since="2026-05-01"`. Returns a trimmed envelope list — the agent picks a UID, calls `get_message` to read the body.

### "Summarize today's unread emails"

`search` with `unseen=true`, `since=today`. Iterate the result list, fetch each, summarize. Default 50-message cap keeps the call bounded.

## Source

- Plugin: [`spark/plugins/builtins/imap_reader.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/builtins/imap_reader.py)
- Discover route: `POST /api/plugin-config/imap_reader/discover`
- Editor: [`spark/web/frontend/src/components/ImapReaderConfigEditor.tsx`](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/components/ImapReaderConfigEditor.tsx)
- Tests: [`tests/unit/test_imap_reader_plugin.py`](https://github.com/Veilfire/EmberSpark/blob/main/tests/unit/test_imap_reader_plugin.py)
