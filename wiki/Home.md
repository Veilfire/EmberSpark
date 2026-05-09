# EmberSpark Wiki

**EmberSpark** is an open-source, local-first, privacy-conscious AI agent runtime for **bounded autonomy**. It is a Python 3.12+ single-agent runtime you own end-to-end, with a mandatory OS sandbox, operator-configurable plugins, and a web UI designed for live iteration.

This wiki is the user-facing documentation. If you came here looking to run EmberSpark, read a plugin reference, or figure out what a specific permission does, you're in the right place. For internal architecture and source-code-level detail, see the [docs/](https://github.com/Veilfire/EmberSpark/tree/main/docs) directory in the main repo.

---

## New to EmberSpark?

Fastest path:

```bash
git clone https://github.com/Veilfire/EmberSpark.git
cd EmberSpark
docker compose up
```

Open the printed URL, save the credentials it prints once, and you're in the web UI. The compose file pre-bakes Bubblewrap, the embedding model, and the privacy NER model — no venv, no system packages.

Then read these in order:

1. **[Getting Started](Getting-Started)** — Docker quick-start (above) and the native venv alternative.
2. **[Installation](Installation)** — full install reference including system dependencies and optional extras.
3. **[First Task](First-Task)** — a guided walkthrough of writing your first agent YAML and running it.
4. **[Web UI Guide](Web-UI-Guide)** — tour the web interface, the command palette, and the eight-section Security Center.

## Core concepts

- **[Bounded Autonomy](Concepts-Bounded-Autonomy)** — the design philosophy behind EmberSpark
- **[The Sandbox](Concepts-Sandbox)** — why every tool call runs in a child process
- **[Plugins](Concepts-Plugins)** — what a plugin is, how it's configured, how it's invoked
- **[Permissions & Grants](Concepts-Permissions)** — the five-layer permission model
- **[Personas](Concepts-Personas)** — hot-reloadable system prompts
- **[Memory](Concepts-Memory)** — task / session / long-term, and how they compose
- **[Continuous Learning](Concepts-Learning)** — reflection, playbooks, and the Thompson bandit
- **[Skills](Concepts-Skills)** — agent-discovered API skills with mandatory human review
- **[Privacy & Redaction](Concepts-Privacy)** — how EmberSpark keeps secrets out of logs and prompts
- **[Budgets](Concepts-Budgets)** — iteration, model-call, tool-call, wall-clock, and cost ceilings

## Working with plugins

- **[Using Plugins](Using-Plugins)** — the operator's guide to the built-in plugins

### Core
- **[Plugin Reference: filesystem](Plugin-Reference-Filesystem)**
- **[Plugin Reference: http_client](Plugin-Reference-HTTP-Client)**
- **[Plugin Reference: http_tool](Plugin-Reference-HTTP-Tool)** — per-host method matrix + readability
- **[Plugin Reference: markdown_writer](Plugin-Reference-Markdown-Writer)**
- **[Plugin Reference: shell](Plugin-Reference-Shell)**
- **[Plugin Reference: sqlite](Plugin-Reference-SQLite)**

### Research & data
- **[Plugin Reference: web_search](Plugin-Reference-Web-Search)** — provider-agnostic search
- **[Plugin Reference: pdf_reader](Plugin-Reference-PDF-Reader)**
- **[Plugin Reference: csv_io](Plugin-Reference-CSV-IO)**
- **[Plugin Reference: json_query](Plugin-Reference-JSON-Query)** — JMESPath filter
- **[Plugin Reference: rss_reader](Plugin-Reference-RSS-Reader)**
- **[Plugin Reference: datetime](Plugin-Reference-Datetime)** — time utilities

### Side effects
- **[Plugin Reference: email_sender](Plugin-Reference-Email-Sender)** — SMTP send
- **[Plugin Reference: git](Plugin-Reference-Git)** — structured git ops
- **[Plugin Reference: image_gen](Plugin-Reference-Image-Gen)** — provider-agnostic image generation
- **[Plugin Reference: webhook](Plugin-Reference-Webhook)** — outbound HMAC-signed POST to allowlisted hosts
- **[Plugin Reference: telegram_messenger](Plugin-Reference-Telegram-Messenger)** — Telegram Bot API (send / edit / keyboards / commands)

### Authoring
- **[Plugin Authoring](Plugin-Authoring)** — write your own plugin

## Security & operations

- **[Permissions Guide](Permissions-Guide)** — the deep dive on how every gate works
- **[Security Center Guide](Security-Center-Guide)** — the tabs of the Security Center, what they do, when to use them
- **[Data Classification Guardrails](Data-Classification-Guardrails)** — per-class allow / warn / redact / shadow_block / block with explicit unlimited grants
- **[Filtering page](Filtering-Page)** — operator surface for the guardrail engine: per-category mask styles (`****-1234`, `J. D.`, `[#hash]`), per-detector enable/disable, paste-and-test dry-run sandbox
- **[Secrets Guide](Secrets-Guide)** — the age-encrypted vault, env fallback, passphrase wrap
- **[Deployment Guide](Deployment-Guide)** — loopback / LAN / public bind modes
- **[Daemon Modes](Daemon-Modes)** — naked venv, Docker, Firecracker microVM
- **[Logging & Tracing](Logging-And-Tracing)** — span flame graphs, retention, hash-chain integrity

## Feature guides

- **[Persona Manager](Persona-Manager-Guide)** — editing the system prompt live
- **[Scheduling](Scheduling-Guide)** — events, chains, approvals, DLQ, webhooks
- **[Webhook Provider Profiles](Webhook-Provider-Profiles)** — GitHub / Slack / Stripe / Linear / Vercel — pick the right `auth_mode` + `body_parser`
- **[Telegram Bot Setup](Telegram-Bot-Setup)** — chatbot UX over Telegram, per-chat / per-user authorization
- **[Cost & Budgets](Cost-And-Budgets)** — tracking spend, setting hard stops
- **[Memory Browser](Memory-Browser)** — inspecting what the agent has learned + retention pruning
- **[Skill Catalog](Skill-Catalog)** — approving agent-discovered API skills
- **[Forensic Review Guide](Forensic-Review-Guide)** — per-run chain-of-thought viewer (admin only)

## Reference

- **[Command Palette & Keyboard Shortcuts](Command-Palette)**
- **[API Reference](API-Reference)** — every HTTP endpoint
- **[Configuration Reference](Configuration-Reference)** — every YAML field
- **[Error Codes](Error-Codes)** — stable identifiers for every classified denial
- **[Troubleshooting](Troubleshooting)**
- **[FAQ](FAQ)**
- **[Contributing](Contributing)**

---

## The EmberSpark values

- **Bounded autonomy** over unlimited agency
- **Local-first** over cloud-by-default
- **Declarative** over magic
- **Auditable** over clever
- **Fail closed** over "good enough"

If any page in this wiki drifts from those values, the page is wrong, not the values.

## License

Apache 2.0.
