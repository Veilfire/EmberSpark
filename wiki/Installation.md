# Installation

The complete install reference. If you only want to run EmberSpark, [Getting Started](Getting-Started) is shorter. Come here when you need to know *why* a step exists or want to tune the install.

---

## Prerequisites

| Requirement | Version | Why |
|---|---|---|
| Operating system | Linux or macOS | Windows is **not** supported — mandatory OS sandbox needs Bubblewrap or Seatbelt |
| Python | 3.12 or 3.13 | Runtime uses async features landed in 3.11+ and type-hint syntax from 3.12 |
| Bubblewrap (Linux) | any recent | The default sandbox backend on Linux |
| sandbox-exec (macOS) | shipped | The default sandbox backend on macOS |
| Disk space | ~1 GB | Python packages + spaCy model + Chroma + SQLite |
| RAM | 2 GB available | Sandbox child process rlimit is 512 MB by default; the main runtime adds overhead |

Optional:

| Optional | Purpose |
|---|---|
| nsjail | Stricter Linux sandbox backend (opt-in per agent) |
| Docker | Only if you want to run EmberSpark as a container |
| Firecracker binary + KVM | Only if you want microVM deployment |
| Ollama | Local LLM provider; runs on `localhost:11434` |

---

## System packages

### Debian / Ubuntu

```bash
sudo apt update
sudo apt install -y \
  bubblewrap \
  python3.12 python3.12-venv python3.12-dev \
  build-essential \
  libffi-dev libssl-dev \
  sqlite3 \
  git curl
```

### Fedora / RHEL

```bash
sudo dnf install -y \
  bubblewrap \
  python3.12 python3.12-devel \
  gcc gcc-c++ make \
  libffi-devel openssl-devel \
  sqlite git curl
```

### Arch

```bash
sudo pacman -S --needed \
  bubblewrap \
  python \
  base-devel \
  libffi openssl \
  sqlite git curl
```

### macOS

```bash
brew install python@3.12 git
# sandbox-exec is part of the base system; nothing to install
```

---

## Optional: nsjail (Linux stricter sandbox)

Only if you want an alternate backend with tighter seccomp and cgroups controls:

```bash
# Debian/Ubuntu: build from source
sudo apt install -y protobuf-compiler libprotobuf-dev libnl-route-3-dev pkg-config flex bison
git clone https://github.com/google/nsjail.git
cd nsjail
make
sudo cp nsjail /usr/local/bin/
```

Then set `sandbox.backend: nsjail` in your agent YAML. It's opt-in per agent, so you can have one agent use Bubblewrap and another use nsjail on the same host.

---

## Python package

### From source (recommended during alpha)

```bash
git clone https://github.com/Veilfire/EmberSpark.git
cd spark
python3.12 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e '.[openai,anthropic,openrouter,ollama,web,dev]'
```

### Optional extras reference

| Extra | Pulls in | When to use |
|---|---|---|
| `openai` | `langchain-openai` | You want to use OpenAI models |
| `anthropic` | `langchain-anthropic` | You want Claude |
| `openrouter` | `langchain-openai` (same SDK, different base URL) | You want to use OpenRouter's model marketplace |
| `ollama` | `langchain-ollama` | You want local models via Ollama |
| `web` | `fastapi`, `uvicorn`, `websockets`, `itsdangerous`, `bcrypt` | You want the web UI (almost certainly yes) |
| `nsjail` | `python-nsjail` | You're using the nsjail backend |
| `dev` | `pytest`, `ruff`, `mypy`, `bandit`, `hypothesis`, ... | You're writing code or running tests |

You can stack them: `pip install -e '.[openai,anthropic,web]'`.

---

## The Presidio spaCy model

EmberSpark's redaction pipeline uses Microsoft Presidio for NER-based PII detection. Presidio needs a spaCy model:

```bash
python -m spacy download en_core_web_lg
```

This is a ~500 MB download, cached in your venv. First use after install will be slower; `spark doctor check` prewarms it.

If you want a smaller install, set `privacy_mode: regex_only` in your agent YAML. The regex + entropy layers still run, but the Presidio layer is skipped.

---

## Verify the install

```bash
spark doctor check
```

Expected output:

```
sandbox backend bubblewrap        (or: seatbelt on macOS)
chromadb ok
presidio ok
```

If any line is missing or red, go back to the corresponding prerequisite.

---

## Upgrading

```bash
cd spark
git pull
source .venv/bin/activate
pip install -e '.[openai,anthropic,openrouter,ollama,web,dev]'
```

No database migrations needed during alpha — `init_db` creates tables as needed. In later releases this will be replaced with Alembic migrations.

If you change providers or extras, re-run pip install with the new extras list. Extras are additive — installing `[web]` doesn't remove `[openai]`.

---

## Uninstalling

EmberSpark stores all runtime state under `~/.spark/`:

- `~/.spark/spark.yaml` — your config
- `~/.spark/spark.db` — SQLite: agents, tasks, runs, memory index, plugin configs, personas, audit log
- `~/.spark/chroma/` — long-term memory vector store
- `~/.spark/logs/` — JSONL logs
- `~/.spark/web-token` — headless auth token
- `~/.spark/web-credentials.json` — bcrypt-hashed web UI password
- `~/.spark/firecracker/` — microVM artifacts (if you used that mode)

Blowing away `~/.spark/` resets everything, **including** the age vault and identity — so make sure you've got a backup if you care about the stored secrets.

```bash
# full reset
rm -rf ~/.spark
pip uninstall spark-runtime
```

The age vault and identity (`~/.spark/secrets.age`, `~/.spark/age_identity.key`) are removed as part of blowing away `~/.spark/` — there's no separate keyring state to clean up.

---

## Troubleshooting

### "sandbox unavailable" on startup

`spark serve` and `spark task run` refuse to start without a working sandbox. If `spark doctor check` reports `sandbox unavailable`:

- **Linux:** confirm `bwrap` is on your PATH (`which bwrap`). Install `bubblewrap` if not.
- **macOS:** confirm `sandbox-exec` works (`sandbox-exec -f /dev/null echo hi` should return). If it errors with "permission denied," you may need to allow unsigned binary execution in System Preferences → Privacy & Security.
- **Windows:** this will never work. Use WSL2 for a Linux environment.

### ImportError for `langchain_openai`

You didn't install the `openai` extra. Add it: `pip install -e '.[openai,web]'`.

### Presidio errors on first run

Either the spaCy model isn't downloaded (`python -m spacy download en_core_web_lg`), or Presidio itself isn't installed. Presidio is part of the base requirements — it should have been pulled in by `pip install -e .`. If not, `pip install presidio-analyzer presidio-anonymizer`.

### "chain broken" from `spark logs verify`

Someone or something modified a log file under `~/.spark/logs/`. This is the hash-chain integrity feature working — see [Logging-And-Tracing](Logging-And-Tracing) for what to do next.

---

## Next steps

- **[Getting Started](Getting-Started)** for the quick-start
- **[Configuration Reference](Configuration-Reference)** for every YAML field
- **[Deployment Guide](Deployment-Guide)** for non-laptop setups (LAN, public, daemon modes)
