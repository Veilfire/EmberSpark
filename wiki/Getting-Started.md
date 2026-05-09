# Getting Started

This page gets you from zero to a running EmberSpark web UI with one configured task in about 10 minutes.

Before you start, make sure you're on **Linux or macOS**. Windows is not supported because EmberSpark's mandatory OS sandbox requires Bubblewrap (Linux) or Seatbelt (macOS).

---

## 1. Install system dependencies

### Linux

```bash
sudo apt update
sudo apt install bubblewrap python3.12 python3.12-venv
```

If you're on a non-Debian distro, install `bubblewrap` (or `bwrap`) from your package manager. EmberSpark needs `bwrap` on the PATH.

### macOS

`sandbox-exec` (Seatbelt) is built in. You only need a recent Python:

```bash
brew install python@3.12
```

---

## 2. Install EmberSpark

Clone the repo and install into a venv:

```bash
git clone https://github.com/Veilfire/EmberSpark.git
cd spark
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e '.[openai,anthropic,openrouter,ollama,web,dev]'
```

Pick whichever provider extras you plan to use. `web` is the extra that installs FastAPI, uvicorn, bcrypt, and websockets. `dev` pulls in the test and lint tooling.

Download the Presidio spaCy model (for PII detection in the privacy pipeline):

```bash
python -m spacy download en_core_web_lg
```

This is a ~500 MB download, one-time. If you want a lean install, you can skip it and set `privacy_mode: regex_only` in your agent YAML.

---

## 3. Verify the sandbox

```bash
spark doctor check
```

Expected output:

```
sandbox backend bubblewrap    (Linux)
sandbox backend seatbelt      (macOS)
chromadb ok
presidio ok
```

If `sandbox unavailable`, your system doesn't have a working backend. Re-check step 1.

---

## 4. Initialize the runtime config

```bash
spark config init
```

This creates `~/.spark/spark.yaml` with the web UI **disabled** — EmberSpark fails closed, so you have to explicitly opt in. Open the file:

```bash
$EDITOR ~/.spark/spark.yaml
```

Change `enabled: false` → `enabled: true`:

```yaml
apiVersion: spark.veilfire.dev/v1alpha1
kind: SparkRuntime
metadata:
  name: default
spec:
  web:
    enabled: true           # ← flip this
    bind:
      mode: loopback
      host: 127.0.0.1
      port: 7777
    credentials:
      rotate_on_startup: true
    session_ttl_seconds: 3600
    rate_limit_per_minute: 120
```

Save.

---

## 5. Start the web UI

```bash
spark serve
```

You'll see something like:

```
Spark web
  bind:  http://127.0.0.1:7777 (mode=loopback)

============================================================
  Spark web UI — credentials (DISPLAYED ONCE; save them now)
============================================================
  URL:      http://127.0.0.1:7777
  Username: sparrow1234
  Password: tree-song77@Moon
============================================================
  These credentials are not logged. If lost, re-run `spark serve`
  with `--rotate-credentials` or set credentials.rotate_on_startup=true
  in ~/.spark/spark.yaml to mint a new pair.
============================================================
```

**Save those credentials now.** They're not logged anywhere. If you miss them, kill the server, run `spark serve --rotate-credentials`, and a new pair is printed.

Open `http://127.0.0.1:7777` in your browser. Sign in with the username and password.

---

## 6. Configure a provider

EmberSpark won't talk to any LLM until you have:

1. An API key stored in the age vault (or the env fallback)
2. An agent YAML that references the key by name

### Store the API key

```bash
spark secrets set anthropic_key    # prompts for value (no echo)
```

The first `spark serve` auto-creates the age vault at `~/.spark/secrets.age`; this command populates it. See the [Secrets Guide](Secrets-Guide) for passphrase wrapping, vault rotation, and the full CLI.

Or use the dev-only env fallback:

```bash
export SPARK_SECRET_ANTHROPIC_KEY='sk-ant-...'
```

Every env fallback resolution emits an info log — fine for a quick try, not durable for production.

### Point your agent YAML at it

Use the example agent:

```bash
cp examples/agents/research-assistant.yaml ~/.spark/my-agent.yaml
$EDITOR ~/.spark/my-agent.yaml
```

The example already references `api_key_ref: anthropic_key`. If you're using a different provider, edit the `provider:` block.

Validate the YAML:

```bash
spark agent validate ~/.spark/my-agent.yaml
```

---

## 7. Run your first task

The simplest test: run the shipped one-shot example.

```bash
spark task run examples/tasks/weekly-digest.yaml --agent ~/.spark/my-agent.yaml
```

Watch the JSON output. In the Web UI, click **Runs** to see the run in the list. Click the run_id to see the **flame graph** of its spans.

---

## Where to go next

- **[Web UI Guide](Web-UI-Guide)** — tour every page of the web interface
- **[Using Plugins](Using-Plugins)** — configure the built-in plugins so your agent can actually do things
- **[Permissions Guide](Permissions-Guide)** — understand how EmberSpark gates tool calls
- **[First Task](First-Task)** — a guided walkthrough of writing your first task from scratch
- **[Configuration Reference](Configuration-Reference)** — every YAML field explained
