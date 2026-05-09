# Installation

The complete install reference. If you only want to run EmberSpark, [Getting Started](Getting-Started) is shorter. Come here when you need to know *why* a step exists or want to tune the install.

EmberSpark ships in two install shapes:

- **Docker / Podman** — the published [`docker-compose.yaml`](https://github.com/Veilfire/EmberSpark/blob/main/docker-compose.yaml) builds a self-contained image with Bubblewrap, the embedding model, the privacy NER model, and every Python wheel pre-installed. **Recommended for production and most evaluation work** — see [Docker install](#docker-install) below.
- **Native venv** — clone the repo, install into a Python 3.12+ virtualenv. Use this when you want to hack on EmberSpark itself or you're deploying onto a host you've already configured for it.

---

## Docker install

```bash
git clone https://github.com/Veilfire/EmberSpark.git
cd EmberSpark
docker compose up               # foreground; first run builds, save the printed credentials
docker compose up -d            # subsequent runs detached
docker compose logs -f spark    # tail logs
docker compose down             # stop, KEEP volumes
docker compose down --volumes   # stop + WIPE all state (vault, DB, deliverables)
```

Persistent state lives in two named volumes:

| Volume | Mounts at | What's there |
|---|---|---|
| `spark-state` | `/data/spark` | web credentials (`web-credentials.json`, `web-token`), age-encrypted secrets vault (`secrets.age`), JSONL logs (`logs/spark.jsonl`), agent + task YAMLs |
| `spark-data` | `/data/spark-volume` | SQLite DB (`spark.db`), Chroma vectors, scratch dir, deliverables dir |

The image runs as the non-root `spark` user (uid 1000). The container's bind mode + source-IP allowlist come from a pre-baked [`deploy/docker/spark.yaml`](https://github.com/Veilfire/EmberSpark/blob/main/deploy/docker/spark.yaml) — LAN bind on `0.0.0.0:7777` with a `192.168.0.0/16` allowlist. Edit that file and recreate the container if you need a different bind / allowlist combination.

Sandbox plumbing: `cap_drop: ALL` + `security_opt: [seccomp=unconfined, apparmor=unconfined]`. Bubblewrap inside the container uses unprivileged user namespaces; the inner sandbox auto-detects nested-userns mode (`/run/.containerenv`) and bind-mounts `/proc` + `/dev` instead of mounting fresh ones — see [Concepts: Sandbox](Concepts-Sandbox).

If your host kernel has `kernel.unprivileged_userns_clone=0`, flip it to `1` (`sudo sysctl kernel.unprivileged_userns_clone=1`); modern 5.x+ kernels default to enabled.

Need to switch to Podman: `podman-compose up` works as a drop-in. The compose file is podman-compatible.

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
| Firecracker binary + KVM | Only if you want microVM deployment |
| Ollama | Local LLM provider; runs on `localhost:11434` |

(Docker / Podman aren't listed — see [Docker install](#docker-install) above; if you're using the container image you don't need any of the per-OS packages below.)

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
