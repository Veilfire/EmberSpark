# Security Center Guide

The Security Center is the multi-tab policy editor at `/security` in the web UI. This page walks each tab and the operator workflows for each. Note: configuration of data-class levels, scopes, mask styles, and per-detector toggles moved to the dedicated [Filtering page](Filtering-Page); the **Data Classes** tab here renders a redirect notice and will be removed in the next release.

All mutations in the Security Center write `audit_log` entries. Critical mutations (freeze, internal-IP grants, raw logging, trusted-doc edits) use `critical` severity and show up in the Incident Banner.

Minimum role:

- **viewer** can read everything
- **operator** can edit per-agent policies and most settings
- **admin** can flip global posture toggles, create webhook triggers, manage trusted docs, grant internal-IP access

---

## Tab 1 — Global Posture

The top-level kill switches for the entire runtime.

### Emergency freeze

A red "Freeze" button. Clicking it:

- Sets `global_posture.frozen = true`
- Sets `freeze_reason` to the text you typed
- Writes a `critical` audit entry
- Causes the engine's `_preflight` to raise `PermissionDenied` on every run that tries to start

While frozen:

- The scheduler keeps ticking but every fire is refused
- Running tasks continue to completion (freeze is preflight-only)
- Chat sessions still work at the message level but new runs are blocked
- The web UI shows a persistent red banner: `⚠ Spark is frozen: <reason>`

**Unfreezing** requires clicking Unfreeze. Audited at `elevated`.

### Compliance mode

Two options:

- **standard** (default) — normal operation
- **audit** — requires operator click-through on every tool call (not yet wired into the engine; placeholder for future use)

### Default privacy mode

The privacy mode new agents inherit when they're first created. Setting this to `strict` (the default) means new agents get full redaction, strict sensitivity gates, and raw logs off.

### Master toggles (elevated)

Two switches that require you to type the literal word `confirm` in a separate field before clicking:

- **Allow internal IPs** — default `false`. When `true`, the default RFC1918 blocklist can be selectively overridden via the Network tab's internal-IP grants. This master must be on for grants to work.
- **Allow raw logging** — default `false`. When `true`, per-agent `raw_prompts` and `raw_model_outputs` can be flipped in the Privacy tab.

Both write `critical` audit entries.

### Good operator habits

- **Practice the freeze once** on a non-critical setup so you know the banner shows up and the unfreeze works. You don't want the first time you need it to be in an incident.
- **Leave `allow_internal_ips` off** unless you have an active grant.
- **Leave `allow_raw_logging` off** unless you're actively debugging a specific issue.

---

## Tab 2 — Network

Per-agent outbound network policy. Pick an agent from the dropdown at the top, then edit the fields.

### Policy fields

- **Allowed hosts** — the FQDN list the agent is allowed to reach via `http_client`. This populates `spec.permissions.network.allow_hosts` in the agent YAML and also serves as a ceiling for `http_client.allow_hosts` in the plugin config.
- **Allow HTTP** — plaintext allowance. Off by default.
- **Max response bytes** — per-request response size ceiling.
- **Connect / read timeouts** — per-request timeouts.

All of these are **staged** when you click Save — they land in the audit log as `security.network.patch` entries. The canonical on-disk YAML is updated separately (see the UI's "Apply to YAML" workflow — or hand-edit for now).

### Internal IP grants

The scary part. By default, any tool call resolving to an RFC1918 / loopback / link-local IP is refused by the SSRF defense. If you need a specific agent to reach a specific internal CIDR for a bounded time, this is the flow:

1. Click **Add grant**.
2. Fill in:
   - **Agent name** (must match exactly)
   - **CIDR** (e.g. `10.0.5.0/24`) — validated by Python's `ipaddress` module
   - **Reason** — free text, audited
   - **TTL hours** — 1–24, default 4
   - **Confirm** — type the agent name *again* to confirm
3. Click Grant.

A `critical` audit entry is written. The grant is stored in `internal_network_grants` with an expiration. The `http_client` plugin consults this table when deciding whether to allow an IP in the CIDR.

After the TTL expires, the grant is inactive and the CIDR is blocked again — no operator action needed.

You can **revoke** an active grant before its TTL expires by clicking Revoke on the row. Audited at `elevated`.

### Operator habits

- **Grants are for debugging and one-offs.** If you find yourself renewing a grant every four hours, something is wrong with the design — either the service should be on a public IP with a proper TLS cert, or the agent shouldn't be calling internal services.
- **The CIDR should be as narrow as possible.** `10.0.5.42/32` is better than `10.0.5.0/24`, which is better than `10.0.0.0/8`.
- **Always add a reason.** Future-you reading the audit log will appreciate it.

---

## Tab 3 — Filesystem

Per-agent filesystem policy. Pick an agent and set:

- **Allow paths** — newline-separated list, matches `spec.permissions.filesystem.allow_paths`
- **Deny paths** — nested denies inside allow paths
- **Max read bytes** — ceiling on any `read` call
- **Max files per call** — ceiling on any `list` call

Same staging behavior as the Network tab. Audited as `security.filesystem.patch`.

Remember: this tab edits the **agent YAML's filesystem permissions**. The **plugin config** for `filesystem` is a different surface — see [Plugin Reference: filesystem](Plugin-Reference-Filesystem). Typically you want both to agree; the plugin config is the more specific narrowing.

---

## Tab 4 — Sandbox

Per-agent sandbox backend and rlimits. Pick an agent and set:

- **Backend** — `auto` / `bubblewrap` / `nsjail` / `seatbelt`. `auto` picks the best available on the host.
- **CPU seconds** — rlimit_cpu
- **Memory MB** — rlimit_as
- **Max open files** — rlimit_nofile
- **Max processes** — rlimit_nproc
- **Timeout seconds** — wall-clock ceiling enforced by the parent via `asyncio.wait_for`

Note that the sandbox **cannot be disabled**. There's no off switch in this tab — by design. The field is shown but read-only with a "mandatory" badge.

### Self-test

Click **Run self-test** to verify the sandbox backend is reachable and functional. The result is a short payload with the backend name and an availability flag. Run this after:

- Upgrading Bubblewrap / nsjail
- macOS system updates (which occasionally break Seatbelt)
- Tuning rlimits to confirm the backend still accepts them

---

## Tab 5 — Plugins

The per-agent plugin allowlist and permission grant matrix.

- **Allowed plugins** — comma-separated, matches `spec.plugins.allow`
- **Permission grants** — comma-separated, matches `spec.permissions.grants`

This is the UI for Layers 1 and 2 of the permission system. Per-plugin config is in the **Plugins** page (not this tab) — see [Plugin Reference: *](Plugin-Reference-Filesystem).

---

## Tab 6 — Privacy

Per-agent privacy mode and raw logging toggles.

- **Privacy mode** — `strict` / `balanced` / `regex_only`
- **Raw prompts** — default `false`; writes critical audit entry when enabled
- **Raw model outputs** — same

The raw logging toggles are **double-guarded**: the master toggle in the Global Posture tab must be on, AND you must flip the per-agent switch, AND the UI asks you to confirm (browser `confirm()` dialog).

When enabled, raw prompt + output content lands in the JSONL log bypassing all redaction. Every enable is audited at `critical` severity.

**Always turn it off again after debugging.** A forgotten `raw_prompts: true` is a compliance problem.

---

## Tab 7 — Data Classes (moved → SECURE → Filtering)

The flat per-class table lived here historically. Configuration of data-class **levels**, **scopes**, **mask styles**, **min confidence**, **consensus toggle**, and **per-detector enable/disable** moved to the dedicated **SECURE → Filtering** page, which also adds a paste-and-test dry-run sandbox.

What remains on Security Center for now:

- **Per-agent overrides** — pick an agent, set a class-level override or revert to inherit the global value. (Will move to Filtering's right rail in a follow-up.)
- **Unlimited grants** — explicit carve-outs. The pattern for "this agent handles credit cards as part of its job, allow `financial.card`." Requires typed-name confirmation, TTL (default 7 days, permanent requires extra danger confirm), audited critical.

This tab itself now renders a one-paragraph notice that points at `/filtering`. It will be removed in the next release.

Full reference:

- [Filtering page](Filtering-Page) — the operator surface.
- [Data Classification Guardrails](Data-Classification-Guardrails) — the engine.

---

## Tab 8 — Secrets

View the list of secret names available to the runtime. Values are **never** shown — this tab only displays names.

### Canary test

Type a secret name and click Test. The runtime attempts to resolve the secret via `secrets.get()` and returns `{"ok": true/false}`. This verifies the secret is reachable without exposing the value.

**Every canary test writes an audit entry** at `info` severity, so rapid-fire enumeration attempts are visible.

### Common workflows

- **Rotating a secret** — update the secret via `spark secrets set <name>`, then click Test in the canary to verify the new value is reachable.
- **Checking for missing secrets** — if you added a new plugin that needs a secret you haven't created yet, canary-test the secret name to confirm it's not there.
- **Pre-flight before a task** — before running a cost-heavy task, canary-test the API key secret to make sure you haven't forgotten to add it.

---

## Tab 9 — Trusted Docs

The allowlist of hosts that the **skill discovery** subgraph is permitted to fetch documentation from. This is a separate list from the agent's `network.allow_hosts`.

- **Default list** — baked in, includes major API providers (GitHub, Slack, Telegram, Notion, Stripe, ...). Read-only.
- **Custom list** — operator-added hosts. Each row has the host, the operator who added it, a timestamp, and an optional note.

Add a host by typing it and clicking Add. Audited at `elevated`. Remove a host by clicking Remove on the row (admin only, audited at `elevated`).

### Why this is separate

Skill discovery is a higher-risk operation than normal tool use. When an agent fetches documentation to learn a new API, you don't want it reaching into every host the agent is normally allowed to talk to. The separation means:

- You can give an agent `api.github.com` in its regular allowlist for data fetching
- But skill discovery is only allowed to fetch from the Trusted Docs list

This is how you prevent the agent from "discovering" a skill that points at the wrong host.

---

## Daily operator routine

Here's what a typical week looks like with the Security Center:

### Monday morning

- Check **Audit Log** — anything critical over the weekend?
- Check **Guardrails** — redactions, denials, budget trips in the last 72h
- Check **Cost & Budgets** — monthly progress

### During the week

- When adding a new capability, use the **Plugins** page to configure the relevant plugin narrowly
- When tuning an agent, use the **Persona** page for voice and the Plugins page for tool scope
- When something surprises you, check the **Runs** page for the flame graph

### Friday evening

- Glance at the **Audit Log** for any `elevated`/`critical` entries
- Confirm no forgotten `raw_prompts: true`
- Confirm no active internal-IP grants that should be revoked

### On incidents

- Click **Freeze** on the Global Posture tab immediately
- Investigate via Runs, Audit Log, Guardrails
- Unfreeze only after you understand what happened

---

## Further reading

- [Concepts: Permissions](Concepts-Permissions) — the abstract model
- [Permissions Guide](Permissions-Guide) — the five-layer deep dive
- [Using Plugins](Using-Plugins) — plugin-specific operator workflows
- [docs/security-posture.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/security-posture.md) — the full threat model
