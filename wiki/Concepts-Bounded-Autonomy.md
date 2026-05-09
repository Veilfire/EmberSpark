# Concept: Bounded Autonomy

"Bounded autonomy" is the phrase that shapes every design decision in EmberSpark. If you only read one concept page, read this one — everything else is a consequence.

---

## The problem

Most agent frameworks optimize for the question: "how much can the agent do on its own?" The answer is usually "as much as possible," and the defaults reflect that — unrestricted filesystem access, broad tool permissions, unlimited retries, opaque prompts.

This is fine for demos. It's bad for real work.

In real work, you need:

- **Predictability.** The same task run twice should behave consistently.
- **Audit.** If something went wrong, you need to reconstruct *why*.
- **Containment.** A mistake should be recoverable, not catastrophic.
- **Honesty.** The agent should not silently expand its own reach.

Unbounded autonomy fails all four. It's unpredictable because the agent can choose from an infinite action space. It's not auditable because the choices are hidden inside the model. Mistakes can be catastrophic because there's nothing between the mistake and the side effect. And the agent can absolutely widen its own reach — it's right there in the training data.

---

## The alternative

Bounded autonomy says: **the agent still gets to make choices, but inside an envelope the operator controls**.

The operator declares:

- Which plugins the agent can call
- Which permissions those plugins are granted
- How many iterations / model calls / tool calls / seconds / dollars the agent can spend
- What the agent's system prompt looks like (via personas)
- How tool outputs are filtered before the model sees them
- Which hosts it can reach, which paths it can touch, which commands it can run
- What gets stored in long-term memory and what stays ephemeral

The model then makes choices **inside** that envelope. The envelope is enforced by five layers (see [Permissions Guide](Permissions-Guide)), and the operator can narrow any layer without touching the others.

---

## What this buys you

**Predictability.** When the agent calls `http_client`, you know exactly which hosts it could have reached. Not "probably GitHub" — literally the list in the plugin config.

**Audit.** Every decision the runtime makes on the agent's behalf writes an event. Permission denied? Logged. Budget exceeded? Logged. Memory promoted? Logged. You can replay any run from the span tree.

**Containment.** If a plugin misbehaves, the OS sandbox catches it at the kernel. If the Python gate fails, the sandbox still holds. If the sandbox fails, the filesystem allowlist in the plugin code still narrows the blast radius. Belt, suspenders, safety pin.

**Honesty.** The operator can narrow an allowlist, and the model **cannot widen it back**. This is the whole point of the `merge_config_and_args` rule in the plugin runtime. If a plugin's `input_schema` and `config_schema` overlap on a field, the operator's value wins at runtime.

---

## Design consequences

Every subsystem in EmberSpark has been shaped by this principle. A short tour:

- **Plugins** declare their permissions, their inputs, their outputs, their sensitivity. Nothing is implicit.
- **The sandbox is mandatory.** You cannot run EmberSpark without a working Bubblewrap / Seatbelt / nsjail backend. `spark serve` refuses to start without one.
- **Memory is distilled, not raw.** Reflection writes *summaries* of what was learned. Raw prompts and raw outputs are off by default and require an elevated audit entry to enable.
- **Budgets are layered.** Iteration, model call, tool call, wall clock, and cost — any one of them can stop a run.
- **The web UI is disabled by default.** Even on a loopback bind, you have to explicitly opt in by setting `spec.web.enabled: true`.
- **Credentials rotate on every `spark serve`.** You see the cleartext password once on startup, then it's bcrypt-hashed.
- **Webhook tokens are bcrypt-hashed on creation.** The cleartext is shown once at creation and never again.
- **Every mutation is audited.** Even canary tests against secrets land in the audit log, so enumeration attempts are visible.

---

## What this does *not* mean

Bounded autonomy is not the same as **no** autonomy. The model still makes real choices:

- Which plugin to call in a given iteration
- What arguments to pass (within the schema the operator allows)
- When to stop looping and return an answer
- Which memory candidates to promote during reflection
- Which playbook to follow (from the bandit-selected set)

The operator defines the envelope. The model steers inside it.

EmberSpark is also not trying to defend against a hostile operator. If you want to compromise your own agent, you can — flip `allow_raw_logging: true`, grant every permission to every plugin, widen `allow_hosts` to the world. The defaults are closed; the operator can open them. That's different from a system that's closed *and* resistant to the operator.

---

## When bounded autonomy hurts

Sometimes it's friction. The agent tries to do something sensible and gets refused because the operator didn't think to allowlist the right host, the right path, the right command. The operator has to go configure the plugin, re-run the task, and often iterate a few times before things work.

This is the price of the trade. The alternative is a system that just does the thing, silently, and you find out three hours later that it hit an unexpected endpoint, wrote to an unexpected path, or spent $47 on a single task.

The tools EmberSpark gives you to reduce the friction:

- **Persona hot reload** — change the agent's voice without restarting anything
- **Plugin config in the UI** — no YAML editing to tune a plugin
- **Run replay with flame graphs** — see exactly which tool call failed and why
- **Guardrails dashboard** — a single page with every permission denial in the last 24h
- **Audit log with filters** — trace any change back to the operator action that made it

---

## The values this implies

- **Bounded autonomy** over unlimited agency
- **Local-first** over cloud-by-default
- **Declarative** over magic
- **Auditable** over clever
- **Fail closed** over "good enough"

Any part of EmberSpark that violates these is a bug, not a feature.

---

## Further reading

- [Permissions Guide](Permissions-Guide) — the five-layer gate in practice
- [Concepts: The Sandbox](Concepts-Sandbox) — why every tool call runs in a child process
- [Concepts: Plugins](Concepts-Plugins) — what a plugin actually is
- [security-posture.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/security-posture.md) — the source-level threat model
