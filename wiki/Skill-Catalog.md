# Skill Catalog

The **Skills** page is where you review and approve agent skills, and where you see the catalog of approved skills per agent.

For the conceptual model, see [Concepts: Skills](Concepts-Skills). For the source-level architecture, see [docs/learning-and-skills.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/learning-and-skills.md).

## What a skill is

Not executable code. A **structured knowledge record** with one of three flavors:

- `kind: api` — how to talk to an external API. `service_name`, `base_url`, `auth_method`, `endpoints[]`, `required_hosts`, `required_secrets`, `source_url`. The agent uses these via the existing `http_client` / `http_tool` plugin.
- `kind: behavior` — a heuristic about *how* the agent should approach a class of problem (claim decomposition, source quality scoring, structured verdict templates). Stored alongside `description`, `rationale`, `examples[]`, optional `success_criteria`.
- `kind: knowledge` — a domain fact or rule the agent wants the runtime to surface back via long-term-memory retrieval on future runs. `description` + `rationale` plus optional `examples`.

All three flavors carry `name`, `description`, `confidence`, `rationale` (where applicable), and live in the same `skill_reviews` table while pending. Approval promotes them to the `skills` table and writes a distilled record into long-term memory so retrieval surfaces them on subsequent runs.

## Where skills come from

| Source | Trigger | Default `kind` |
|--|--|--|
| **Discovery engine** | Operator-triggered crawler over trusted-doc hosts | `api` |
| **`propose_skill` plugin** | Agent calls it from its tool-call loop during a chat turn or task run | `api` / `behavior` / `knowledge` (agent picks) |

Both paths land rows in the same review queue. The pending-review UI has filter chips so you can drain by flavor.

## Pending review queue

The top section of the Skills page. Filter chips at the top: **all / api / behavior / knowledge** with row counts.

Each pending skill shows as a card with:

- **Kind chip** (`api` / `behavior` / `knowledge`)
- **Headline** — service name (`api`) or proposed name (`behavior` / `knowledge`)
- **Source agent** — which agent staged it
- **Confidence** percentage
- **Rationale** — block quote (when the agent provided one — required for `propose_skill`, empty for legacy discovery rows)
- **Name** (editable) — defaults to the AI-generated label
- **Description** (editable)
- **Examples** (when present, for `behavior`/`knowledge` skills)
- **Success criteria** (when present)
- **Base URL** (read-only, `api` only)
- **Required hosts** and **required secrets** (`api` only)
- **Source URL** (click-through; only rendered when it's a real `http(s)://` link — `propose_skill` synthesizes `agent-proposal://...` sentinels for non-API kinds)
- **Review notes** (editable)

Two buttons:

- **Approve** — writes a `SkillRow` to the `skills` table and promotes a distilled record into long-term memory. Elevated audit entry.
- **Reject** — marks the review as `rejected` with your notes. Info audit entry.

## The approval workflow

The questions you ask depend on the `kind`:

**For `api` skills** (mostly from the discovery engine):

1. **Read the source URL.** Is it actually the canonical doc page, or is the agent pointing at something tangential? If the latter, reject with a note.
2. **Read the proposed name and description.** Does the AI's label accurately describe what the skill does? Rewrite if not.
3. **Check `required_hosts`.** These are what will need to be in the agent's `network.allow_hosts` for the skill to work. If you don't want the agent calling those hosts at all, reject.
4. **Check `required_secrets`.** If the skill needs `telegram_bot_token` and you don't have one in the age vault yet, you'll need to add it via `spark secrets set telegram_bot_token` before using the skill.
5. **Read the endpoints.** Do they make sense? If the agent hallucinated non-existent endpoints, reject.

**For `behavior` skills** (from `propose_skill`):

1. **Read the rationale.** Does it identify a real failure mode the agent currently has, or is it generic self-improvement-flavored text? Reject vague proposals — they waste review cycles.
2. **Read the examples.** Are they concrete enough that you can imagine the agent applying them? If they're hand-wavy, reject and ask the agent to re-propose with sharper examples (`dedupe_strategy=update_pending` makes that one round trip).
3. **Read the success criteria** (if present). Does it tell you how to know the heuristic is paying off?

**For `knowledge` skills** (from `propose_skill`):

1. **Verify the fact.** Is it actually true and useful? Approving promotes it into long-term memory, so it'll resurface on future runs.
2. **Check it's not already known.** Use the Memory Browser to grep for the same claim under the agent's namespace.

Then click **Approve** or **Reject**.

## Approved skills

The bottom section lists approved skills for a specific agent. Enter an agent name in the input. The table shows:

| Column | What it is |
|---|---|
| `name` | The final name the operator approved |
| `service_name` | Service label |
| `auth_method` | Auth style |
| `required_hosts` | Hosts the agent needs in its network allowlist |
| `required_secrets` | Secrets the agent needs |
| `uses` | How many times the agent has referenced this skill in a run |
| `status` | `approved` or `disabled` |
| `approved_by` | Reviewer subject |
| `approved_at` | Approval timestamp |

Operator actions on approved skills:

- **Disable** — flip `status` to `disabled` so the skill stops being retrieved into the agent's context. Audited at `elevated`.

## The two-allowlist guarantee

Approving a skill is **necessary but not sufficient** for the agent to use it. The second requirement:

- The target API host must be in the agent's regular `network.allow_hosts`

If you approve a Telegram skill but the agent doesn't have `api.telegram.org` in its allowlist, the agent's attempt to use the skill will fail at the SSRF defense with `network_denied`. This is deliberate — approving a skill says "I trust this knowledge"; adding a host says "I authorize the network call." They're separate decisions.

See [Security Center Guide](Security-Center-Guide) → Trusted Docs for where the skill-discovery allowlist lives.

## Further reading

- [Concepts: Skills](Concepts-Skills)
- [docs/learning-and-skills.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/learning-and-skills.md)
- [Permissions Guide](Permissions-Guide) — how skill usage composes with the network gate
