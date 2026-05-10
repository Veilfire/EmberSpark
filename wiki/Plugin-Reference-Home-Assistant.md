# Plugin: `home_assistant`

View states and (opt-in) call services on a Home Assistant instance.
Because HA's built-in **HomeKit Controller** integration bridges Apple
HomeKit accessories, this single plugin covers HomeKit *and* the rest
of HA's ecosystem (Z-Wave, Zigbee, Matter, Hue, Nest, …) through the
same surface.

| | |
|---|---|
| **Required permissions** | `NET_HTTP`, `SECRETS_READ` |
| **Sensitivity** | `MODERATE` |
| **Network** | Yes — talks to HA over HTTP/HTTPS |
| **Output filtered** | Yes — responses pass through the redaction chain (location attributes are caught by Presidio before reaching the model) |

## What this plugin will not do for v1

- **Pair HomeKit accessories directly** — HomeKit-on-Linux requires
  `aiohomekit` plus mDNS pairing UX; the path is "install Home
  Assistant → enable its HomeKit Controller integration → talk to HA
  through this plugin." Direct HomeKit support is on the future-work
  list, not in this release.
- **Persistent WebSocket subscriptions** — HA's WebSocket API gives
  push state updates, but the request/response tool model has no place
  for a long-lived subscription. Future "watcher" plugin would be a
  separate pattern.
- **HA-to-Spark notifications** — receiving HA automations as Spark
  triggers is a separate, already-supported flow (Scheduler →
  triggers → webhook).

## Bootstrap

### 1. Long-lived access token

In Home Assistant: **Profile → Security → Long-Lived Access Tokens →
Create Token**. Copy the value (it's shown once).

On the Spark host:

```bash
spark secrets set home_assistant_token
# Paste the token at the prompt; the value lands in the age vault.
```

### 2. Network policy

HA almost always lives at an RFC1918 / loopback address. Two changes
in **Security Center → Network**:

- Add the HA hostname (e.g. `ha.lan`) to the agent's `allow_hosts`.
- Issue a time-bound **internal-IP grant** for HA's CIDR (typed-confirm).

If you skip this step, the plugin's first call raises
`SPK_E_URL_PRIVATE_IP` and the Failure Inspector deep-links to the
internal-IP grant flow with the agent / host pre-filled.

### 3. Plugin allowlist

Add `home_assistant` to the agent's `plugins.allow` (Security Center →
Plugins).

## Configuration — the live editor

The Plugins page shows a custom editor for `home_assistant` (every
other built-in renders as a generic schema-driven form). The first
time you open it with a `base_url` + `token_secret` set, it calls the
discovery endpoint, hits `/api/config`, `/api/services`, and
`/api/states` in your HA instance, and renders **checkbox grids
populated from live data**:

- **Connection** — base URL, token-secret name, Verify SSL toggle,
  **Test connection & discover** button. Discovery failures (missing
  secret, internal-IP refused, etc.) render an inline Failure
  Inspector with the same deep-links runtime errors get.
- **Read-only** — single toggle. When on, `call_service` always
  refuses; agent can read everything.
- **Allowed domains** — checkbox grid grouped by category (Lights &
  switches / Sensors / Media / Climate / Security & access / Location
  & people / Other). Each row shows risk chip + entity count. Six
  domains carry a **danger** chip and require a typed-confirm modal
  before activation:
  - `lock`, `alarm_control_panel`, `camera`, `device_tracker`,
    `person`, `vacuum`
- **Allowed services** — per-domain matrix. Only domains in
  `allowed_domains` are interactive. Services classified
  `danger`/`elevated`/`safe`; danger services trip the same
  typed-confirm modal.
- **Entity excludes** — typeahead multi-select over the live entity
  list, plus a glob input (e.g. `device_tracker.*`) for bulk excludes.

Saved config is plain string lists / dicts; the discovery layer only
drives the editor UX. Runtime enforcement reads `allowed_domains`,
`allowed_services`, `entity_filter_glob`, and `read_only` from the
saved config the same way every other plugin does.

## Action surface (model-callable)

| Action | What it calls | Required config | Notes |
|---|---|---|---|
| `list_states` | `GET /api/states` | none | Filtered by `allowed_domains` + `entity_filter_glob`; capped at `max_states_returned` (default 200). `verbose=true` includes full attribute blobs. |
| `get_state` | `GET /api/states/{entity_id}` | entity's domain in `allowed_domains` | Returns full state. |
| `call_service` | `POST /api/services/{domain}/{service}` | `read_only=false` AND `service ∈ allowed_services[domain]` | Body: `entity_id` (string or list) + arbitrary `data` kwargs. |
| `render_template` | `POST /api/template` | none | Jinja2 templates. Useful for "how many lights are on?" aggregations. |
| `get_history` | `GET /api/history/period/…?filter_entity_id=…` | entity's domain in `allowed_domains` | Capped at 24h. Per-entity filter is **required** (HA returns the world otherwise). |

## Failure surface

Every refusal path raises `SparkError` with a stable `ErrorCode`. The
[Failure Inspector](Failure-Inspector-Guide) maps each one to a
concrete tuning option:

| Refusal | Code | Inspector deep-link |
|---|---|---|
| 401 from HA (token rejected) | `SPK_E_SECRET_NOT_FOUND` | `/secrets` |
| Plugin-level domain exclusion | `SPK_E_PERMISSION_MISSING` (`missing_domain`) | `/plugins?prefill=…` — domain checkbox flashed + ticked; danger domains trigger the typed-confirm modal automatically |
| `read_only=true` blocked call_service | `SPK_E_PERMISSION_MISSING` (`missing_toggle: read_only`) | `/plugins?prefill=…` — read-only toggle flashed + flipped to `false` |
| Service not in `allowed_services[domain]` | `SPK_E_PERMISSION_MISSING` (`missing_service`) | `/plugins?prefill=…` — service matrix cell flashed + ticked |
| 404 entity / unknown service | `SPK_E_INPUT_SCHEMA_INVALID` | Caller-side fix; advisory only |
| Connect refused on RFC1918 | `SPK_E_URL_PRIVATE_IP` | `/security?tab=network` (existing prefill) |
| Host not in agent's `allow_hosts` | `SPK_E_URL_DENIED` | `/security?tab=network` (existing prefill) |

## Sensitivity & redaction

- Sensitivity defaults to `MODERATE`; `filter_output_before_model=true`
  runs the redaction chain on every response. Location attributes
  (`latitude`, `longitude`) on `device_tracker.*` entities flow
  through Presidio's `LOCATION` recognizer.
- `device_tracker`, `person`, `camera`, `lock`, `alarm_control_panel`,
  and `vacuum` are excluded by default. Operators must explicitly
  allow each.
- Even with everything allowlisted, the plugin's response is bounded
  by `max_response_bytes` (default 1 MiB) and `list_states` is capped
  at `max_states_returned` (default 200) — large homes don't blow the
  prompt window with one tool call.

## Audit story

- Successful read calls land in the standard tool-call audit row at
  `info`.
- `call_service` against safe / elevated domains: extra
  `kind=plugin.home_assistant.call` row at `info` with
  `{domain, service, entity_id}`.
- `call_service` against danger domains: same kind at `elevated`
  severity.
- Discovery (every editor open / refresh): one `info`-severity row
  under `kind=security.plugin.discover` with hit-counts only.

## Recipes

### "What lights are on?"

`read_only=true`, `allowed_domains` includes `light`. Agent calls
`list_states` filtered to `light.*`, returns the names whose `state`
is `on`.

### "Turn off the kitchen light"

Bootstrap path:

1. First call refuses with `read_only=true`. Inspector deep-link →
   editor opens with the read-only toggle flashed + flipped off.
2. Save → second call still refuses; service `light.turn_off` not in
   `allowed_services`. Inspector deep-link → editor opens with the
   `light × turn_off` matrix cell flashed + ticked.
3. Save → call succeeds; audit row at `info`.

The same shape applies for any device class: ask for it, refuse, take
the inspector's suggestion, save, retry.

### "Where is my phone?"

Default config refuses (`device_tracker` excluded). The right answer
is usually "the agent shouldn't have this", but if you want it:

1. Ask in chat. Refusal is `SPK_E_PERMISSION_MISSING` with
   `missing_domain=device_tracker`.
2. Inspector deep-link → editor opens, `device_tracker` checkbox
   flashed + ticked + the typed-confirm modal already up. Type
   `device_tracker` and confirm.
3. Save. Sensitive attributes (`latitude`, `longitude`) still get
   redacted by the data-class pipeline before reaching the model;
   what the agent sees is `[REDACTED:LOCATION]`. Add a more permissive
   data-class policy on `pii.basic` for this agent if you want the
   coordinates through.

## Out of scope

- Direct HomeKit pairing (use HA's HomeKit Controller integration).
- WebSocket state subscriptions.
- HA → Spark notifications via this plugin (use a webhook trigger).
- Multi-instance HA (config supports one `base_url`).

## Source

- Plugin: [`spark/plugins/builtins/home_assistant.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/builtins/home_assistant.py)
- Discover route: [`spark/web/api/plugin_config.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/api/plugin_config.py) (`POST /api/plugin-config/home_assistant/discover`)
- Editor: [`spark/web/frontend/src/components/HomeAssistantConfigEditor.tsx`](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/components/HomeAssistantConfigEditor.tsx)
- Tests: [`tests/unit/test_home_assistant_plugin.py`](https://github.com/Veilfire/EmberSpark/blob/main/tests/unit/test_home_assistant_plugin.py)
