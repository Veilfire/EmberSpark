# EmberSpark

An open-source, local-first, privacy-conscious AI agent runtime for **bounded autonomy**.

EmberSpark is a Python 3.12+ runtime for a single agent you own end-to-end. It is YAML-configured, plugin-driven, and built around the idea that an agent runtime should not assume the model deserves to see everything, do everything, store everything, or access everything. It is meant for real work, not demos.

## Why EmberSpark

Most agent frameworks default to "wide open, trust the model." EmberSpark defaults to **closed**. Every tool the agent uses declares its permissions, its inputs, its outputs, and its sensitivity. Every side effect runs inside a mandatory OS sandbox. Secrets live outside the model's context by construction. Memory is distilled, not hoarded. Logs are operator-visible without becoming a transcript archive.

You get a capable agent runtime that you can explain to yourself, defend to a reviewer, and trust with your own filesystem.

## Highlights

- **LangGraph 1.1** execution with `BudgetGuard` (iterations / model calls / tool calls / **tokens** / wall-clock / cost) and atomic per-run finalization
- **Four LLM providers** from day one: OpenAI, Anthropic, OpenRouter, Ollama (local)
- **Mandatory OS sandbox** for every tool call — Bubblewrap (Linux default), `sandbox-exec`/Seatbelt (macOS), or `nsjail` (Linux strict). EmberSpark refuses to start without a working backend.
- **17 built-in plugins**, including new external integrations:
    - Core: `filesystem`, `http_client`, `markdown_writer`, `shell`, `sqlite`, `web_search`, `pdf_reader`, `csv_io`, `json_query`, `rss_reader`, `datetime`, `email_sender`, `git`, `image_gen`, `http_tool`
    - **External integrations:** `webhook` (outbound HMAC-signed POST), `telegram_messenger` (Bot API send / edit / inline keyboards / commands)
- **Inbound webhook triggers** — generic, with three pluggable knobs (`auth_mode`: bearer / hmac_sha256 / hmac_sha256_slack; `body_parser`: json / form / raw; `event_filter`: dotted-path matching). Covers GitHub, Slack, Stripe, Linear, Vercel, Netlify, Twilio, generic. Auto-handles Slack URL-verification handshakes.
- **Telegram chatbot integration** — long-poll bot runner with per-chat agent bindings, per-user authorization, conversational + command modes, built-in `/help` `/runs` `/run` `/cancel` `/whoami`, custom slash commands, autocomplete via `setMyCommands`, typing-indicator + edit-the-placeholder UX.
- **Run replay with outcome** — every run page renders the planner's final response as markdown, plus a sidebar of deliverable artifacts linked back to the run. `task.spec.output: file` writes the answer to `<deliverables>/<task>/<run_id>.md` automatically.
- **Task chaining** via `on_success` / `on_failure` with cycle detection + depth cap of 5
- **Persona manager with hot reload** — the system prompt is re-read from the DB on every model call, so persona tweaks take effect on the very next turn.
- **SSRF-hardened HTTP** with IDN normalization, IP-pinning transport, cloud-metadata blocklist, DNS-rebinding defense
- **Path-traversal-hardened filesystem** with `realpath` + `is_relative_to` + `O_NOFOLLOW` + refused symlink parents
- **Privacy-by-default redaction** — `detect-secrets` + custom regex (now including Telegram bot tokens) + Microsoft Presidio (NER-based PII), all on by default
- **Structured JSONL logging** with SecretStr unwrapping, pattern scrubbing, correlation IDs, span timing, and a **hash-chained** retention layer (`hot → warm → cold → archive`)
- **Three-tier memory** — task (ephemeral), session (SQLite), long-term (Chroma) with per-namespace isolation
- **Continuous learning** — reflection candidates, Thompson-sampling playbook bandit, and agent-discovered API skills staged for human review
- **Full web UI** — scheduler with task creator + visual cron builder + webhook trigger creator, chat, cost & budgets, memory browser, skill catalog, 8-section Security Center, persona editor, plugin config, stats, guardrails, **run replay with flame graph + final response + deliverables**, audit log, ops
- **Three deployment modes** — venv (naked), Docker, Firecracker microVM

## Architecture snapshot

```
 CLI (Typer) → SparkRuntime YAML (~/.spark/spark.yaml)
                    ↓
 Persona Manager → Runtime Engine (LangGraph)
                    ↓            ↓         ↓
              Privacy Filter   Memory    Reflection + Learning
                    ↓
            ToolExecutor  ─── Plugin Config (DB-backed, UI-edited)
                    ↓
         Sandbox Executor → bwrap / sandbox-exec / nsjail
                    ↓
               Tool process
```

Every side effect passes through two enforcement layers: the Python `ToolExecutor` seam (allowlists, schemas, budgets, redaction) and the OS sandbox (namespaces, rlimits, scoped bind mounts). A compromised plugin cannot reach beyond its declared permissions even if the Python layer is bypassed.

## Install

```bash
# Core + providers you want
pip install spark-runtime[openai,anthropic,openrouter,ollama,web]

# System deps
# Linux:
sudo apt install bubblewrap
# macOS: sandbox-exec is built in

# Presidio spaCy model (first time only)
python -m spacy download en_core_web_lg
```

## Quickstart

```bash
spark config init                            # writes ~/.spark/spark.yaml (web disabled)
$EDITOR ~/.spark/spark.yaml                  # set spec.web.enabled: true
spark doctor check                           # verify sandbox + deps
spark serve                                  # start the web UI
```

`spark serve` prints generated credentials **once** to stderr on startup:

```
============================================================
  Spark web UI — credentials (DISPLAYED ONCE; save them now)
============================================================
  URL:      http://127.0.0.1:7777
  Username: sparrow1234
  Password: tree-song77@Moon
============================================================
```

Save those, open the URL, sign in. The UI walks you through the rest.

### CLI-only flow

```bash
spark agent validate examples/agents/research-assistant.yaml
spark task run examples/tasks/weekly-digest.yaml \
  --agent examples/agents/research-assistant.yaml
spark logs tail
spark logs verify                            # check hash-chain integrity
spark memory query research-assistant "what did we decide last week"
spark skills review                          # list pending skill reviews
```

## Documentation

| Topic | Where |
|---|---|
| **Getting started** | [wiki/Getting-Started.md](wiki/Getting-Started.md) |
| **Tools & permissions** *(start here)* | [docs/tools-and-permissions.md](docs/tools-and-permissions.md) |
| **Security posture & threat model** | [docs/security-posture.md](docs/security-posture.md) |
| **Plugin config reference** | [docs/plugin-config.md](docs/plugin-config.md) |
| **Persona manager** | [docs/persona-manager.md](docs/persona-manager.md) |
| **Scheduling** | [docs/scheduling.md](docs/scheduling.md) |
| **Webhook provider profiles** | [wiki/Webhook-Provider-Profiles.md](wiki/Webhook-Provider-Profiles.md) — GitHub / Slack / Stripe / Linear / Vercel / generic configs |
| **Telegram bot setup** | [wiki/Telegram-Bot-Setup.md](wiki/Telegram-Bot-Setup.md) — chatbot UX, per-user auth, command routing |
| **Logging & tracing** | [docs/logging-and-tracing.md](docs/logging-and-tracing.md) |
| **Learning + skills** | [docs/learning-and-skills.md](docs/learning-and-skills.md) |
| **Web UI reference** | [docs/web-ui.md](docs/web-ui.md) |
| **Deployment + daemons** | [docs/deployment.md](docs/deployment.md) |
| **Writing a plugin** | [docs/plugin-authoring.md](docs/plugin-authoring.md) |
| **GitHub wiki** | [wiki/](wiki/) — staging for the project's GitHub wiki. See [docs/wiki-sync.md](docs/wiki-sync.md). |

## Status

Alpha. v1 core built + feature expansion F1–F6 landed, plus run-output rendering, generic webhook trigger system, and Telegram chatbot integration.

Platforms: **Linux + macOS**. Windows is out of scope because the mandatory OS sandbox requires Bubblewrap or Seatbelt.

## Project values

- **Bounded autonomy** over unlimited agency
- **Local-first** over cloud-by-default
- **Declarative** over magic
- **Auditable** over clever
- **Fail closed** over "good enough"

## License

Apache 2.0.
