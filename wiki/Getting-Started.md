# Getting Started

This page gets you from zero to a running EmberSpark web UI with one configured task in about 10 minutes.

Before you start, make sure you're on **Linux or macOS**. Windows is not supported because EmberSpark's mandatory OS sandbox requires Bubblewrap (Linux) or Seatbelt (macOS).

You have two paths:

- **[Docker (recommended)](#docker-quick-start)** — one `docker compose up` and you're done. The image bakes in Bubblewrap, the spaCy NER model, the embedding model, and every Python dependency. No venv, no system packages, no Python version dance.
- **[Native install](#native-install)** — clone the repo, install into a venv, run from your shell. Use this if you want to hack on EmberSpark itself or you're already running on a Linux box you control.

---

## Docker quick-start

The shortest path from zero to a working web UI. Tested with Docker Engine + Compose v2 on Linux, Docker Desktop on macOS, and `podman` + `podman-compose` on either.

### 1. Clone

```bash
git clone https://github.com/Veilfire/EmberSpark.git
cd EmberSpark
```

### 2. Bring it up

```bash
docker compose up
```

(or `podman-compose up` on Podman.)

First run builds the image — ~5–10 min: Bubblewrap, Python wheels, the frontend bundle, and the HuggingFace embedding-model preload. Subsequent runs reuse the cache and start in seconds.

When the runtime is up you'll see:

```
============================================================
  Spark web UI — credentials (DISPLAYED ONCE; save them now)
============================================================
  URL:      http://0.0.0.0:7777
  Username: …
  Password: …
============================================================
```

**Save those credentials.** They rotate on `--rotate-credentials`, but you only see *this* pair once. Open the URL, sign in.

### 3. Detach

After copying the credentials, `Ctrl+C` and bring the stack back up in the background:

```bash
docker compose up -d
docker compose logs -f spark    # tail logs / see credentials again on a restart
```

### What you got

- **Web UI on the LAN** — `0.0.0.0:7777` with a `192.168.0.0/16` source-IP allowlist baked into [`deploy/docker/spark.yaml`](https://github.com/Veilfire/EmberSpark/blob/main/deploy/docker/spark.yaml). Public exposure requires editing that file.
- **Two named volumes for state** — `spark-state` (web credentials, age vault, logs) and `spark-data` (SQLite, Chroma vectors, deliverables). `docker compose down` keeps both; `docker compose down --volumes` wipes them.
- **Sandbox preconfigured** — `cap_drop: ALL` + `seccomp=unconfined` + `apparmor=unconfined` so Bubblewrap's unprivileged-userns mode works without further tuning. See [Concepts: Sandbox](Concepts-Sandbox) for why this combination is the right one.
- **Per-host healthcheck** — `GET /api/health` every 30 s; `docker ps` flips to `(healthy)` once startup completes.

### 4. Configure your provider

Sign into the UI, then:

1. **Provider** sidebar → pick OpenAI / Anthropic / OpenRouter / Ollama / Bedrock, paste an API key. The key is sealed into the age-encrypted vault on the `spark-state` volume.
2. **Persona** → tweak the default system prompt if you want.
3. **Plugins** → most plugins ship with safe working defaults; `web_search` defaults to `ddg_html` (no API key needed). For an end-to-end fact-checker walkthrough, jump to [First Task](First-Task).

That's the Docker path. The rest of this page is the **native install** alternative — skip it unless you specifically want a venv-based setup.

---

## Native install

### 1. Install system dependencies

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
