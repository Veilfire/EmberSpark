# Your First Task

A guided walkthrough of writing an agent from scratch, configuring the plugins it needs, and running a task end-to-end. Estimated time: 20 minutes.

By the end, you'll have an agent that fetches data from a public GitHub endpoint and writes a markdown summary to disk. Along the way you'll see every one of EmberSpark's permission layers in action.

---

## What we're building

**Goal:** an agent that, on command, calls `https://api.github.com/repos/{owner}/{repo}` and writes a markdown note about the repository to `~/spark-workspace/notes/`.

**Plugins needed:**

- `http_client` — to call the GitHub API
- `markdown_writer` — to write the note

**Permissions needed:**

- `net.http` — for outbound HTTPS
- `secrets.read` — in case we later want to authenticate
- `fs.write` — for the markdown file

**Budgets:** generous, since this is a test: 10 iterations, 10 model calls, 10 tool calls, 5 minutes.

---

## Step 1: Prepare the workspace

Create the workspace directory:

```bash
mkdir -p ~/spark-workspace/notes
```

This is where the agent will write output. **The plugin will refuse to create parent directories**, so you must create it yourself.

---

## Step 2: Write the agent YAML

Create `~/.spark/agents/repo-summarizer.yaml`:

```bash
mkdir -p ~/.spark/agents
$EDITOR ~/.spark/agents/repo-summarizer.yaml
```

Paste:

```yaml
apiVersion: spark.veilfire.dev/v1alpha1
kind: Agent
metadata:
  name: repo-summarizer

spec:
  description: >
    Fetches a GitHub repository's metadata and writes a markdown summary.

  runtime:
    provider:
      type: anthropic
      model: claude-opus-4-6
      api_key_ref: anthropic_key
      temperature: 0.2
    max_iterations: 10
    max_model_calls: 10
    max_tool_calls: 10
    max_runtime_seconds: 300
    privacy_mode: strict
    reflection: true

  memory:
    task_memory: true
    session_memory:
      enabled: true
      max_entries: 50

  plugins:
    allow:
      - http_client
      - markdown_writer

  permissions:
    filesystem:
      allow_paths:
        - ~/spark-workspace
      deny_paths: []
    network:
      allow_hosts:
        - api.github.com
    sandbox:
      enabled: true
      backend: auto
      cpu_seconds: 30
      memory_mb: 512
      timeout_seconds: 60
    grants:
      - net.http
      - secrets.read
      - fs.write

  logging:
    level: info
    raw_prompts: false
    raw_model_outputs: false
```

A quick annotation of what each block does:

- **`runtime.provider`** — Anthropic. The `api_key_ref: anthropic_key` is a *handle* to a secret in the age vault; the actual key never appears in the YAML.
- **`runtime.privacy_mode: strict`** — full redactor chain, tool outputs filtered, raw prompt logs off.
- **`memory.session_memory`** — lets the agent retain context within a run (but not for long).
- **`plugins.allow`** — this is the **Layer 1 allowlist**. Only these two plugins can be called. Shell, sqlite, filesystem (direct) are all denied.
- **`permissions.filesystem.allow_paths`** — the path sandbox. The markdown_writer plugin will only write under `~/spark-workspace`.
- **`permissions.network.allow_hosts`** — the network sandbox. Only `api.github.com` is reachable.
- **`permissions.sandbox`** — rlimits for the sandboxed child process.
- **`permissions.grants`** — the **Layer 2 grant set**. If this were missing `net.http`, the `http_client` plugin would be refused at the first call.

---

## Step 3: Validate the YAML

```bash
spark agent validate ~/.spark/agents/repo-summarizer.yaml
```

If there's a typo or a field out of place, the validator tells you exactly where. Clean output looks like:

```
Agent 'repo-summarizer' is valid
```

---

## Step 4: Store your Anthropic key

```bash
spark secrets set anthropic_key     # prompts for value (no echo)
```

Or the env fallback (dev / CI only — every resolution is logged):

```bash
export SPARK_SECRET_ANTHROPIC_KEY='sk-ant-YOUR-KEY-HERE'
```

---

## Step 5: Configure the plugins from the Web UI

This is the important step. The agent YAML gave the plugins permission *to exist*, but each plugin still needs its own operator config to actually do anything useful.

1. Start the server: `spark serve` (save the credentials from stderr).
2. Open `http://127.0.0.1:7777` in your browser, sign in.
3. Click **Plugins** in the sidebar.
4. Select **http_client** on the left.
5. Set:
   ```
   allow_hosts:        api.github.com
   allow_http:         (unchecked)
   allowed_methods:    GET
   max_response_bytes: 2000000
   connect_timeout_seconds: 5
   read_timeout_seconds:    15
   user_agent:         spark-first-task/1.0
   ```
6. Type a **reason** ("first task setup") and click **Save**.
7. Select **markdown_writer** on the left.
8. Set:
   ```
   allow_paths:     ~/spark-workspace/notes
   deny_paths:      (empty)
   allow_append:    (checked)
   allow_overwrite: (checked)
   ```
9. Reason: "first task setup". Save.

That's it for plugin config. Note two things:

1. You **narrowed** the plugin to exactly what this agent needs. Even if the model tried to widen `allow_hosts` or point at a different path, the operator config would win.
2. Every save writes an `elevated`-severity audit entry. Click **Audit Log** in the sidebar to see it.

---

## Step 6: Write the task YAML

Create `~/.spark/tasks/summarize-spark-repo.yaml`:

```yaml
apiVersion: spark.veilfire.dev/v1alpha1
kind: Task
metadata:
  name: summarize-spark-repo

spec:
  agent: repo-summarizer
  mode: one_shot

  objective: >
    Fetch the public metadata for the repository "Veilfire/EmberSpark" from
    https://api.github.com/repos/Veilfire/EmberSpark and write a concise markdown
    summary to ~/spark-workspace/notes/spark-repo.md covering the
    description, star count, default branch, and last push date.

  output:
    type: file
    path: ~/spark-workspace/notes/spark-repo.md

  budgets:
    max_runtime_seconds: 180
    max_model_calls: 6
    max_tool_calls: 6
```

Validate:

```bash
spark task validate ~/.spark/tasks/summarize-spark-repo.yaml
```

---

## Step 7: Run it

```bash
spark task run ~/.spark/tasks/summarize-spark-repo.yaml \
  --agent ~/.spark/agents/repo-summarizer.yaml
```

The output goes into the Web UI's **Runs** page as well as stdout. When it finishes:

```bash
cat ~/spark-workspace/notes/spark-repo.md
```

You should see a markdown summary of the repository.

---

## Step 8: Inspect what happened

Open the Web UI.

### Runs page

Click the run_id. The **Run Replay** page shows:

- The **flame graph** of spans: `run → prepare_context → plan → tool_call → plan → tool_call → ...`
- The iteration-by-iteration timeline
- Model calls, tool calls, iterations used
- Duration of each node

### Audit Log

Click **Audit Log** in the sidebar. You'll see:

- Your two `plugin.config.update` entries from step 5
- The automatic `task.started` and `task.completed` events for this run

### Guardrails

Click **Guardrails**. Probably nothing critical — which is the point. If there were permission denials during the run, they'd show up here with clickable filters into the audit log.

### Cost

Click **Cost & Budgets**. You'll see the token spend from this run, broken down by provider and model.

---

## What just happened?

Walking back through the five-layer gate, for the one tool call to `http_client`:

1. **Layer 1 (allowlist)** — `http_client` was in `plugins.allow`. ✓
2. **Layer 2 (grants)** — the plugin required `{net.http, secrets.read}`; the agent granted both. ✓
3. **Layer 3 (budgets)** — first tool call, well under the ceiling. ✓
4. **Layer 4 (plugin config merge)** — the operator's `allow_hosts: [api.github.com]` won over any model-supplied value. ✓
5. **Layer 5 (sandbox)** — the child process was spawned in Bubblewrap (or Seatbelt on macOS) with network namespace shared (because `http_client.needs_network=True`) but filesystem restricted to the workspace bind mount.

Then the SSRF defense in `validate_url` pinned the outbound connection to the resolved IP of `api.github.com` and forbade any redirects.

If you had made a typo in `plugins.allow`, step 1 would have blocked. If you forgot the `net.http` grant, step 2. If you set the budget to 0, step 3. If the operator had narrowed `allow_hosts` to exclude GitHub, step 4. And if someone tried to disable the sandbox, `spark serve` would have refused to start in the first place.

---

## Iterate

Now try changing things:

1. **Tweak the persona.** Go to **Persona** in the sidebar, edit the system prompt to be more concise, click **Save & Activate**, re-run the task. The new voice lands immediately.
2. **Narrow further.** In the **Plugins** page, change `http_client.max_response_bytes` to `100000` (100 KB). Re-run. If the response is bigger than that, you'll see a truncation.
3. **Break something deliberately.** Remove `api.github.com` from `http_client.allow_hosts`. Re-run. Watch the task fail with a classified `network_denied` error. Check the Guardrails page — the denial shows up under `permission_denied`.
4. **Turn on a budget.** Go to **Cost & Budgets**, create a daily budget of $0.01 for the `anthropic` provider with `hard_stop: true`. Re-run. EmberSpark refuses to fire the task because the budget is exceeded. Delete the budget to unblock.

---

## Where to go next

- **[Using Plugins](Using-Plugins)** — the full operator guide to each built-in
- **[Permissions Guide](Permissions-Guide)** — the five-layer deep dive
- **[Persona Manager Guide](Persona-Manager-Guide)** — hot-reload workflow for iterating on the agent's voice
- **[Scheduling Guide](Scheduling-Guide)** — make this recurring, chain it to another task, add a webhook
