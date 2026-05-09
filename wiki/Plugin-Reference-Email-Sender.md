# Plugin Reference: `email_sender`

SMTP send-only email plugin. The operator locks the sender address, the SMTP server, and the recipient domain allowlist. The model can only set subject, body, recipients (subject to the allowlist), and attachments (subject to a path gate).

- **Required permissions:** `net.http`, `secrets.read`, `fs.read` (attachments)
- **Required secrets:** two — SMTP username + password, via `username_secret` / `password_secret`
- **Sensitivity:** `HIGH` — emails can exfiltrate data
- **Network:** required
- **Dependencies:** stdlib `smtplib` + `email.message`; pydantic `EmailStr` needs `email-validator`

---

## Why this is HIGH sensitivity

Emails are a data-exfiltration channel. Every knob that tightens this plugin is worth tightening:

1. **`from_address` is operator-locked.** The model cannot forge a different sender.
2. **`allowed_to_domains` is operator-locked.** When set, every recipient must be under one of these domains. Set it to `["your-company.com"]` to prevent the agent from emailing anyone outside your org.
3. **`attachment_allow_paths` is operator-locked.** Attachments must come from allowlisted directories (typically the data volume's scratch or deliverables subdirectories).
4. **`allow_html` defaults to false.** Plain text only by default — HTML bodies can carry tracking pixels and exfiltrate info on open.

---

## Configuration fields

| Field | Type | Default | Meaning |
|---|---|---|---|
| `smtp_host` | string | required | e.g. `smtp.fastmail.com` |
| `smtp_port` | int | `587` | |
| `use_starttls` | bool | `true` | |
| `username_secret` | string | `smtp_username` | Keyring secret for SMTP username. |
| `password_secret` | string | `smtp_password` | Keyring secret for SMTP password. |
| `from_address` | email | required | The envelope sender. Operator-locked. |
| `allowed_to_domains` | list of strings | `[]` | Empty means no domain filter. Non-empty means every recipient domain must be in the list. |
| `max_subject_chars` | int | `200` | |
| `max_body_chars` | int | `100_000` | |
| `max_recipients` | int | `10` | |
| `allow_html` | bool | `false` | When false, HTML bodies are refused. |
| `allow_attachments` | bool | `true` | |
| `attachment_allow_paths` | list of paths | `[]` | Attachments must live under one of these. |
| `max_attachment_bytes` | int | `10_000_000` | |

---

## What the model sends per call

```json
{
  "to": ["alice@example.com"],
  "subject": "Weekly research digest",
  "body": "Here are this week's highlights:\n\n...",
  "attachments": ["/data/spark-volume/deliverables/digest-2026-04-14.md"]
}
```

The `to` list cannot exceed `max_recipients`, each recipient's domain must be in `allowed_to_domains` (if set), and each attachment path must be under `attachment_allow_paths`.

---

## Operator workflow

**Store the secrets in the age vault:**

```bash
spark secrets set smtp_username    # prompts (no echo)
spark secrets set smtp_password    # prompts (no echo)
```

**Typical config** — send scheduled digest emails from the Spark agent account to your personal inbox:

```json
{
  "smtp_host": "smtp.fastmail.com",
  "smtp_port": 587,
  "use_starttls": true,
  "from_address": "spark-agent@example.com",
  "allowed_to_domains": ["example.com"],
  "max_recipients": 1,
  "allow_html": false,
  "allow_attachments": true,
  "attachment_allow_paths": ["/data/spark-volume/deliverables"]
}
```

**Test against MailHog first.** Point `smtp_host` at a local MailHog container before wiring the real SMTP server. Gmail / Fastmail errors are hard to debug; MailHog shows you the exact message the agent produced.

**Pair with the scheduler.** A typical flow: cron → agent builds a markdown report via `markdown_writer` into deliverables → `email_sender` ships it.

---

## Common pitfalls

- **Bad credentials** — the SMTP server will return `535`. The plugin wraps this in `PermissionError` with the server's message.
- **Domain-not-in-allowlist** — the plugin rejects before connecting to SMTP. No wasted network round-trip.
- **Attachment outside allow paths** — `PathDenied` is raised; no attachment is read.
- **HTML body with `allow_html: false`** — refused before sending.

---

## Further reading

- [Plugin Reference: markdown_writer](Plugin-Reference-Markdown-Writer) — typically produces the body / attachment
- [Scheduling Guide](Scheduling-Guide) — scheduled report generator workflow
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md)
