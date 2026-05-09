# Concept: Skills

A **skill** is a structured record describing how to talk to an external API. It is not executable code. It's a Pydantic document:

- `name`, `description` — AI-labeled at discovery time, editable at review
- `service_name`, `base_url`, `auth_method`
- `endpoints` — list of known `{method, path, description, ...}`
- `required_hosts`, `required_secrets`
- `source_url` — where the documentation was fetched
- `confidence`

Skills live in SQLite (the `skills` table for approved, `skill_reviews` for pending) and, after approval, also in long-term memory as `pattern`-type records for semantic retrieval.

---

## Why skills are knowledge, not code

The obvious alternative is "generate a Python plugin." EmberSpark deliberately does not do this. Generated code is:

- Hard to review before it runs
- A new execution surface that bypasses the existing safety seams
- Inconsistent with the mandatory-sandbox posture

Instead, skills are **structured knowledge** the planner can see in its context. The agent already has the `http_client` plugin; the skill tells it "to send a Telegram message, POST to `https://api.telegram.org/bot<token>/sendMessage` with these params." The actual call still goes through `http_client`, which still runs inside the sandbox, which still enforces the SSRF defense.

No new code paths. No new permissions. Just new knowledge.

---

## Where skills come from

Two paths feed the same review queue:

### A. The discovery loop (operator-triggered, API skills)

When the model encounters a capability it doesn't know — "I need to send a Slack message but I don't know how" — the `SkillDiscovery` subgraph fires:

1. **Plan** — the model proposes a service name and a canonical documentation URL. (`_DiscoveryPlan` Pydantic model.)
2. **Trusted-docs gate** — the proposed doc host is checked against the `TrustedDocPolicy`. This is a **separate allowlist** from the agent's normal `network.allow_hosts` — skill discovery can only fetch from hosts the operator has pre-approved as trustworthy documentation sources.
3. **Fetch** — the `http_client` plugin fetches the doc page. Same SSRF defense, same sandbox.
4. **Extract** — the model parses the doc text into a strict `ApiSkill` via `with_structured_output`. Required fields (auth method, base URL, endpoints) must be present or validation fails.
5. **Stage for review** — the skill is written to `skill_reviews` with `state=pending`, an AI-generated name and description, and the full payload as JSON. `kind=api`.

### B. Agent self-improvement (`propose_skill` plugin)

When the agent decides during a turn that it wants a new skill — *"I'd be more reliable if I had a Claim Decomposition heuristic"* — it calls the `propose_skill` plugin from its tool loop. The plugin lands a `SkillReviewRow` in the same queue with `kind=behavior` (a heuristic / how-to-think rule), `kind=knowledge` (a domain fact the agent wants the runtime to surface back), or `kind=api` (a service the agent wants integrated, equivalent shape to the discovery flow).

Required arguments include a **rationale** — *why* the skill is worth approving. Vague proposals get rejected fast.

Defenses:

- Operator config `enabled` toggle (master switch)
- `max_pending_per_agent` cap (default 20) — beyond this the plugin refuses with a clear error
- `cooldown_seconds` window + `dedupe_strategy` (`reject_duplicate` | `update_pending`) — prevents the agent spamming the queue with re-phrasings of the same idea
- Cross-field validation: `kind=api` requires a real HTTPS `base_url`; `kind=behavior` requires at least one concrete example

Both paths route through the same review UI and approval logic. The agent **cannot** bypass review — there's no code path where a skill goes live without human approval.

---

## Human review

The operator sees pending skills in the web UI's **Skills** page. Each pending card shows:

- Proposed name and description (editable)
- Service name and base URL (read-only — operator can't rewrite the URL)
- Auth method (bearer / api_key_header / oauth2 / ...)
- Required hosts and secrets
- Confidence score
- Source URL (click-through to the raw doc)
- Review notes field

Actions:

- **Approve** — writes a `SkillRow` to `skills`, promotes a distilled record to long-term memory, audits at `elevated` severity.
- **Reject** — marks the review `rejected` with the reviewer's notes, audits at `info` severity.

Approved skills are retrievable by the planner on future runs via the `retrieved_memories` section of the system prompt. They're tagged `[SKILL]` so the model knows they're different from general memories.

---

## The second-allowlist guarantee

Using a learned skill requires *two* things:

1. The skill is approved.
2. The target API host is allowed at the **plugin** layer (e.g. `http_tool` rules, `webhook.allow_hosts`, `telegram_messenger` chat-ids). The agent YAML's `network.allow_hosts` is advisory and operators usually mirror the plugin-level allowlist there for documentation.

If the operator approves a Telegram skill but the `telegram_messenger` plugin config doesn't list `api.telegram.org` (and the bot token isn't in the vault), the next attempt to use the skill fails inside the plugin with `network_denied` / `secret_not_found`. Approving a skill says "I trust this knowledge." Configuring the plugin says "I authorize the network call." They're separate decisions.

---

## Safety properties

- **No code generation.** Skills are data, not executable.
- **Trusted-docs allowlist is separate.** Skill discovery can't be weaponized to fetch arbitrary URLs through the agent's normal grants.
- **Human review is mandatory.** Default is `require_human_review: true`; we deliberately did not ship an "autonomous mode."
- **AI labels are editable.** The reviewer can rewrite the name and description before approval.
- **Every approval is audited.** Critical severity for approvals.
- **Target host still gates usage.** Approving a skill without an allowlisted target is harmless.

---

## Further reading

- [Skill Catalog](Skill-Catalog) — operator guide to the review queue
- [docs/learning-and-skills.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/learning-and-skills.md) — full architecture
- [Concepts: Learning](Concepts-Learning) — how skills fit with reflection and playbooks
- [Security Center Guide](Security-Center-Guide) — managing the trusted-docs allowlist
