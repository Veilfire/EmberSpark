# Configuration Reference

EmberSpark has three YAML kinds. This page lists every field in each.

- **`Agent`** — one agent, defined in a YAML file you point `spark task run --agent` at
- **`Task`** — one task (one-shot / recurring / perpetual / event) referencing an agent by name
- **`SparkRuntime`** — runtime-wide settings (web bind mode, daemon mode), at `~/.spark/spark.yaml`

All three use `apiVersion: spark.veilfire.dev/v1alpha1` and a `kind:` field. Pydantic validates with `extra="forbid"` — typos are hard errors.

---

## `Agent`

```yaml
apiVersion: spark.veilfire.dev/v1alpha1
kind: Agent
metadata:
  name: my-agent       # lowercase, ^[a-z0-9][a-z0-9._-]*$, max 128

spec:
  description: "..."    # free text

  runtime:
    provider:
      type: openai|anthropic|openrouter|ollama
      model: "..."
      temperature: 0.2
      max_tokens: null | int (1..200000)
      timeout_seconds: 60.0
      # provider-specific fields:
      # openai: api_key_ref, base_url, organization
      # anthropic: api_key_ref
      # openrouter: api_key_ref, referer, app_title
      # ollama: base_url, api_key_ref (optional)
    max_iterations: 12            # 1..200
    max_model_calls: 30           # 1..500
    max_tool_calls: 25            # 0..500
    max_runtime_seconds: 900      # 1..86400
    max_tokens_per_run: null      # null = unbounded; 1..10_000_000 otherwise
                                  # sum of input + output tokens across every
                                  # model call in the run; raises
                                  # SPK_E_BUDGET_TOKEN_EXCEEDED on cap
    privacy_mode: strict          # strict | balanced | regex_only
    reflection: true

  memory:
    task_memory: true
    session_memory:
      enabled: true
      max_entries: 200            # 1..10000
    long_term_memory:             # optional
      enabled: true
      namespace: my-agent
      backend: chroma
      collection: my_agent_memory
      persist_path: ~/.spark/chroma
      embedder:
        provider: sentence_transformers
        model: BAAI/bge-small-en-v1.5
      retrieval:
        top_k: 6
        min_score: 0.72
        recency_weight: 0.1
        confidence_weight: 0.1
      retention:
        default_class: review    # persistent | review | temporary | expiring

  plugins:
    allow: [filesystem, http_client, markdown_writer, shell, sqlite]
    config: {}                    # legacy; use /api/plugin-config/ instead

  permissions:
    filesystem:
      allow_paths: ["~/workspace"]
      deny_paths: ["~/.ssh"]
      max_read_bytes: 5000000
      max_files_per_call: 256
    network:
      allow_hosts: ["api.github.com"]
      allow_http: false
      max_response_bytes: 5000000
      connect_timeout_seconds: 5.0
      read_timeout_seconds: 15.0
    shell:
      enabled: false              # hard default
    sandbox:
      enabled: true               # cannot be false
      backend: auto               # auto | bubblewrap | nsjail | seatbelt
      cpu_seconds: 30             # 1..3600
      memory_mb: 512              # 16..16384
      max_open_files: 128         # 4..65536
      max_processes: 8            # 1..256
      timeout_seconds: 60         # 1..3600
    grants:
      - fs.read
      - fs.write
      - fs.list
      - net.http
      - secrets.read
      - subprocess

  logging:
    level: info                   # debug | info | warning | error
    raw_prompts: false            # audited at critical if true
    raw_model_outputs: false      # audited at critical if true
    local_path: ~/.spark/logs
```

---

## `Task`

```yaml
apiVersion: spark.veilfire.dev/v1alpha1
kind: Task
metadata:
  name: my-task

spec:
  agent: my-agent
  mode: one_shot | recurring | perpetual | event
  # Mode-aware schedule constraints (validator):
  #   one_shot   — schedule optional; start_at allowed (delayed); end_at rejected
  #   recurring  — schedule + start_at + end_at required; finite window
  #   perpetual  — schedule + start_at required; end_at rejected
  #   event      — schedule rejected; fires from external triggers
  schedule:
    type: cron | interval
    # cron:
    expression: "0 8 * * 1"        # 5-field crontab
    timezone: America/Vancouver
    # interval:
    # seconds: 3600
    # Optional window endpoints (ISO-8601, tz-aware). Required for
    # mode: recurring; required (start_at only) for mode: perpetual.
    start_at: "2026-06-01T00:00:00+00:00"
    end_at:   "2026-09-01T00:00:00+00:00"
  objective: "Do the thing."
  inputs: {}                      # dict of str|int|float|bool
  session:
    name: null
    continuity: none              # none | bounded | full
  output:
    type: file | stdout | memory
    path: null                    # required if type=file
  budgets:                        # per-task overrides; null = inherit from agent
    max_runtime_seconds: null
    max_model_calls: null
    max_tool_calls: null
    max_tokens_per_run: null

  # F5 additions — all optional, backward compatible
  on:                             # event trigger (one of these shapes)
    # file_changed:
    type: file_changed
    path: ~/Documents/inbox
    recursive: true
    debounce_seconds: 5
    # http_new_row:
    # type: http_new_row
    # url: https://...
    # allow_hosts: [api.example.com]
    # poll_seconds: 300
    # key_path: id
    # telegram_bot — full chatbot UX with per-chat agent bindings:
    # type: telegram_bot
    # bot_token_secret: telegram_bot_token       # vault secret name
    # bindings:
    #   - chat_id: 123456789                     # DM with the operator
    #     agent: research-assistant
    #     allow_user_ids: [42]                   # Telegram user_id whitelist
    #     mode: conversational                   # or command_only
    #     allow_run_tasks: [weekly-digest]       # /run allowlist (empty = disabled)
    #     allow_cancel: false                    # /cancel opt-in
    # commands:
    #   - command: digest
    #     description: Run the weekly digest
    #     action: run_task
    #     task: weekly-research-digest
    # poll_seconds: 10
    # long_poll_timeout: 25
    # typing_indicator: true
  on_success: another-task        # chained successor on success
  on_failure: alert-task          # chained successor on failure (cycle-detected, depth cap 5)
  retry:
    max_attempts: 3               # 1..20
    backoff_seconds: 5.0          # 0..3600
    backoff_multiplier: 2.0       # 1..10
    jitter_seconds: 2.0           # 0..60
  approval:
    required: false
    note: ""
  only_between: "22:00-06:00 America/Vancouver"  # optional run window
  heartbeat_seconds: 60           # required for perpetual with heartbeat
```

---

## `SparkRuntime`

At `~/.spark/spark.yaml` by default. Generated by `spark config init`.

```yaml
apiVersion: spark.veilfire.dev/v1alpha1
kind: SparkRuntime
metadata:
  name: default

spec:
  web:
    enabled: false                # fail-closed default
    bind:
      mode: loopback              # loopback | lan | public

      # loopback:
      host: 127.0.0.1
      port: 7777

      # lan:
      # host: 0.0.0.0
      # port: 7777
      # allowed_cidrs: ["192.168.1.0/24"]
      # trusted_proxies: []

      # public (requires TLS):
      # host: 203.0.113.10
      # port: 443
      # allowed_cidrs: []
      # trusted_proxies: []
      # tls:
      #   cert_file: ~/certs/spark.crt
      #   key_file: ~/certs/spark.key

    credentials:
      rotate_on_startup: true
      display_once: true
      path: ~/.spark/web-credentials.json

    rate_limit_per_minute: 120
    session_ttl_seconds: 3600       # default 1h; min 60, max 30 days (2_592_000)
    # The YAML value is the **initial** timeout for fresh installs. Admins
    # can override it at runtime from Settings → Security (days/hours/minutes
    # or fully disable). The override persists in the `session_settings`
    # table and is applied hot — no restart required.

  daemon:                         # optional
    mode: naked | docker | firecracker

    # naked:
    service_name: spark
    restart_on_failure: true
    venv_path: ~/.spark/venv
    log_dir: ~/.spark/daemon-logs

    # docker:
    # image: spark-runtime:latest
    # container_name: spark
    # volumes: ["spark-state:/data/spark"]
    # network_mode: bridge
    # restart: unless-stopped
    # memory_mb: 2048
    # cpus: 2.0

    # firecracker:
    # firecracker_binary: /usr/local/bin/firecracker
    # rootfs: ~/.spark/firecracker/rootfs.ext4
    # kernel: ~/.spark/firecracker/vmlinux
    # vcpus: 2
    # memory_mib: 1024
    # tap_device: spark-tap0
    # host_cidr: 192.168.241.1/30
    # guest_ip: 192.168.241.2
    # forwarded_ports:
    #   7777: 7777
```

---

## Plugin config

Not in YAML — lives in the `plugin_configs` SQLite table and is edited via the Plugins page of the web UI or `PUT /api/plugin-config/{name}`. See [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md) for the complete field reference of every built-in.

---

## Permission enum

From [`spark/config/enums.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/config/enums.py):

| Value | Scope |
|---|---|
| `fs.read` | Filesystem read / list / stat |
| `fs.write` | Filesystem write / append / create |
| `fs.list` | Directory list only |
| `net.http` | Outbound HTTPS (http_client) |
| `subprocess` | Subprocess spawn (shell) |
| `secrets.read` | Secret manager get |

## Sensitivity enum

| Value | Effect |
|---|---|
| `low` | Tool output flows freely |
| `moderate` | Default — output goes through privacy filter |
| `high` | Blocked from long-term memory in strict mode |
| `restricted` | Blocked from model context and memory in strict mode |

## Privacy mode enum

| Value | What it changes |
|---|---|
| `strict` | Full redaction chain, strict sensitivity gates, raw logs off |
| `balanced` | Full chain, relaxed sensitivity gates, raw logs still off |
| `regex_only` | Skip Presidio (lean install), regex + entropy only |

## Task state enum

`created`, `scheduled`, `running`, `paused`, `sleeping`, `completed`, `failed`, `stopped`, `dlq`

---

## Further reading

- [Getting Started](Getting-Started) — walkthrough using these configs
- [First Task](First-Task) — a concrete example
- [docs/plugin-config.md](https://github.com/Veilfire/EmberSpark/blob/main/docs/plugin-config.md) — plugin config field reference
