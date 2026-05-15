# Plugin: `cloud_drive`

Provider-centric cloud storage. The plugin owns the credential store
(secrets in the Spark vault); rclone is wrapped as an implementation
detail. Configure providers in **Plugins → cloud_drive**; the plugin
synthesizes a fresh rclone config in a per-call temp file on every
action, resolving secrets at call time so credential rotations land
immediately.

| | |
|---|---|
| **Required permissions** | `SUBPROCESS`, `FS_READ`, `FS_WRITE` |
| **Sensitivity** | `MODERATE` |
| **Network** | Yes |
| **Output filtered** | Yes |

## Providers (v1)

| Provider | `kind` | Auth |
|---|---|---|
| Google Drive | `drive` | OAuth (paste-token) |
| OneDrive | `onedrive` | OAuth (paste-token) |
| Dropbox | `dropbox` | OAuth (paste-token) |
| Proton Drive | `protondrive` | user/pass (+ optional 2FA) |

## Actions

| Action | Notes |
|---|---|
| `list_providers` | Discovery — enabled providers and their `allowed_paths`. |
| `list` | Folder listing. `recursive` toggle. |
| `search` | Glob match across the subtree. |
| `get` | Downloads into the agent's scratch dir. Gated by `file_type_allowlist`. |
| `put` | Uploads. Read-only gate. Triggers `auto_share` post-step. Size-capped. |
| `delete` | Read-only gate. |

## Config schema

```yaml
cloud_drive:
  providers:
    - name: gdrive_work          # operator slug; becomes the rclone remote name
      enabled: true
      auth:
        kind: drive              # discriminator: drive | onedrive | dropbox | protondrive
        token_secret: gdrive_work_token
        client_id: ""             # optional; strongly recommended
        client_secret_secret: ""
        team_drive: ""            # empty = personal Drive
      allowed_paths:               # required allowlist; empty refuses all
        - "Spark-agent"
      auto_share:
        enabled: false
        recipients: ["operator@example.com"]
        permission: reader         # reader | writer | commenter

  read_only: true                  # global; blocks put / delete
  max_file_bytes: 52428800         # 50 MB
  file_type_allowlist: [pdf, txt, doc, docx, xls, xlsx, png, jpeg]
```

### Provider type detail

#### `drive` — Google Drive

| Field | Required | Notes |
|---|---|---|
| `token_secret` | Yes | Vault entry holding the JSON blob from `rclone authorize "drive"`. |
| `client_id` | No | Your own Google OAuth client ID. Avoids rclone's shared-client rate-limits. |
| `client_secret_secret` | No (required with `client_id`) | Vault entry holding the client secret. |
| `team_drive` | No | Shared Drive ID. Empty = personal Drive. |

#### `onedrive`

| Field | Required | Notes |
|---|---|---|
| `token_secret` | Yes | From `rclone authorize "onedrive"`. |
| `drive_type` | Yes (default `personal`) | `personal` / `business` / `documentLibrary` (SharePoint). |
| `drive_id` | Yes for business / SharePoint | The drive's GUID. |
| `client_id` + `client_secret_secret` | No | Your own OAuth client. |

#### `dropbox`

| Field | Required | Notes |
|---|---|---|
| `token_secret` | Yes | From `rclone authorize "dropbox"`. |
| `client_id` + `client_secret_secret` | No | Your own OAuth app. |

#### `protondrive`

| Field | Required | Notes |
|---|---|---|
| `username` | Yes | Proton account email. |
| `password_secret` | Yes | Vault entry holding the Proton password (app-specific if 2FA active). |
| `twofa_secret` | No | Vault entry holding the TOTP code or seed. |

## Bootstrap

### OAuth providers (Drive / OneDrive / Dropbox)

1. **On a machine with a browser** (your laptop), run:
   ```
   rclone authorize "drive"            # or "onedrive" / "dropbox"
   ```
   rclone opens the provider's consent screen; approve and copy the
   JSON blob it prints.
2. **Save the token** to the Spark vault under a chosen name:
   ```
   spark secrets set gdrive_work_token
   # paste the full JSON blob (single line)
   ```
3. **Plugins → cloud_drive → Add provider → Google Drive.** Fill
   `token_secret` with the vault name. Add an `allowed_paths` root.
4. **(Recommended)** Register your own OAuth client at
   <https://rclone.org/drive/#making-your-own-client-id> and fill
   `client_id` + `client_secret_secret`.

### Proton Drive

1. Save the account password (app-specific if 2FA enabled) to the vault:
   ```
   spark secrets set proton_drive_password
   ```
2. If 2FA is on, save the TOTP seed too:
   ```
   spark secrets set proton_drive_2fa
   ```
3. **Plugins → cloud_drive → Add provider → Proton Drive.** Fill
   `username`, `password_secret`, and (optionally) `twofa_secret`.

## File-type allowlist

The picker organizes ~70 common extensions into named buckets
(Documents / Office / Images / Archives / Media / Code / Data).
Buckets are a UI primitive only — the config stores a flat extension
list. Defaults: `pdf, txt, doc, docx, xls, xlsx, png, jpeg`.

Toggle whole buckets (tri-state checkboxes) or drill in to add /
remove specific extensions. Anything outside the bucket registry can
be added under **Custom extensions**.

## Auto-share

When `auto_share.enabled` and `put` succeeds, the plugin grants the
configured recipients access to the just-uploaded file using the
provider's native API.

- **`drive`** — Drive Permissions API
  (`POST /drive/v3/files/{id}/permissions`). `permission` → `reader`
  / `writer` / `commenter`. Honors `team_drive` (sends
  `supportsAllDrives=true` for Shared Drives).
- **`onedrive`** — Microsoft Graph
  (`POST /me/drive/items/{id}/invite` for personal,
  `/drives/{drive_id}/items/{id}/invite` for business / SharePoint).
  `permission` → `read` / `write`; commenter folds to read (Graph has
  no commenter role). `sendInvitation: false` — no email notification.
- **`dropbox`** — Dropbox Sharing
  (`POST /2/sharing/add_file_member`). `permission` → `viewer` /
  `editor` / `viewer` (commenter; standard viewer can comment).
  `quiet: true` — no email notification.
- **`protondrive`** — no API-driven sharing (end-to-end encrypted;
  sharing requires the recipient to be a Proton user). Field stays
  available but the post-put step returns no grants.

### Token refresh

OAuth providers issue short-lived access tokens (~1 hour). rclone
refreshes them on every call using the refresh token embedded in the
stored blob, then rewrites the config file on disk. After each
rclone invocation the plugin compares the post-call `token = ` line
against the value it wrote pre-call; any change is persisted back to
the same vault entry. The operator doesn't have to re-paste tokens
unless the refresh token itself is revoked.

A failed write to the vault doesn't fail the rclone call — the next
hour's invocations will silently 401, surfacing as empty
`shared_with` lists. Operators should monitor the audit log for
`security.plugin_config.update` events that DON'T appear on a regular
cadence as a heuristic for stuck token refresh.

## Failure surface

| Refusal | Code | Inspector deep-link |
|---|---|---|
| Provider not enabled | `SPK_E_PERMISSION_MISSING` | `/plugins?plugin=cloud_drive&prefill=…` — provider card flashed |
| `read_only=true` blocks write | `SPK_E_PERMISSION_MISSING` | Read-only toggle flashed |
| Path outside `allowed_paths` | `SPK_E_PATH_DENIED` | Provider card's `allowed_paths` row flashed |
| Path traversal (`..`) | `SPK_E_PATH_TRAVERSAL` | (caller-side fix — no override) |
| File extension not in allowlist | `SPK_E_FILE_TYPE_DENIED` | File-type bucket picker flashed |
| Too-large `put` | `SPK_E_FILE_TOO_LARGE` | `max_file_bytes` row |
| Token / password missing | `SPK_E_SECRET_NOT_FOUND` | Secrets page deep-link |
| rclone binary missing | `SPK_E_SANDBOX_UNAVAILABLE` | (operator must install rclone) |
| rclone timeout | `SPK_E_SANDBOX_TIMEOUT` | `timeout_seconds` row |

## Source

- Plugin: [`spark/plugins/builtins/cloud_drive.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/builtins/cloud_drive.py)
- Editor: [`spark/web/frontend/src/components/CloudDriveConfigEditor.tsx`](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/components/CloudDriveConfigEditor.tsx)
- Provider registry: [`spark/web/frontend/src/components/cloud_drive/ProviderTypeRegistry.ts`](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/components/cloud_drive/ProviderTypeRegistry.ts)
- Tests: [`tests/unit/test_cloud_drive_plugin.py`](https://github.com/Veilfire/EmberSpark/blob/main/tests/unit/test_cloud_drive_plugin.py)
