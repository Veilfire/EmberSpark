# Plugin: `calendar`

View and (opt-in) write calendar events over CalDAV. One plugin
covers every major calendar service that speaks CalDAV: **iCloud**,
**Google Calendar**, **Outlook / Office 365**, **Nextcloud**,
**FastMail**, **mailbox.org**, **Posteo**, **Zoho**, etc.

| | |
|---|---|
| **Required permissions** | `NET_HTTP`, `SECRETS_READ` |
| **Sensitivity** | `MODERATE` (titles + locations + attendees flow through Presidio before reaching the model) |
| **Network** | Yes — speaks HTTP/HTTPS to a CalDAV server |
| **Output filtered** | Yes |

## What it does

| Action | What it calls | Required config | Notes |
|---|---|---|---|
| `list_calendars` | CalDAV PROPFIND on principal | none | Returns every calendar the operator's account exposes. Used by the Plugins-page editor; also model-callable. |
| `list_events` | CalDAV REPORT (calendar-query) | calendar in `allowed_calendars` | `start` + `end` window across allowed calendars. Trimmed view: `{uid, calendar, title, start, end, location, attendees}`. |
| `get_event` | CalDAV GET | event's calendar in `allowed_calendars` | Single event by URL. |
| `create_event` | CalDAV PUT (new resource) | `read_only=false` AND `default_calendar` in `allowed_calendars` | Inputs: `title`, `start`, `end`, optional `description`, `location`, `attendees`, `all_day`. |
| `delete_event` | CalDAV DELETE | `read_only=false` AND event's calendar in `allowed_calendars` | By URL. |
| `find_free_slots` | Internal — runs `list_events` then computes gaps | `allowed_calendars` populated | `window_start` + `window_end` + `duration_minutes`. Returns gaps where no busy event exists. |

## Bootstrap

### 1. App-specific password

CalDAV servers require an **app-specific password** — not your account login. Where to get one:

| Provider | Where to generate |
|---|---|
| **iCloud** | appleid.apple.com → Sign-In and Security → App-Specific Passwords |
| **Google** | myaccount.google.com → Security → App passwords (2FA must be on) |
| **Outlook** | account.live.com → Security → App passwords |
| **FastMail** | settings → Privacy & Security → Integrations → App Passwords |
| **Nextcloud** | Settings → Security → Devices & sessions → Create new app password |

### 2. Store in vault

```bash
spark secrets set calendar_password
# Paste the generated app password at the prompt.
```

### 3. Plugin config

Open `/plugins` → **calendar**. Fill in:

- **CalDAV base URL** — provider-specific, e.g. `https://caldav.icloud.com`, `https://www.google.com/calendar/dav/`, `https://nextcloud.example.com/remote.php/dav/`
- **Username** — usually the email address
- **Password secret name** — defaults to `calendar_password`

Click **Test connection & discover**. The plugin hits the CalDAV principal endpoint and renders a **checkbox grid** of every calendar the account exposes. Tick the ones the agent should reach.

For write operations:

- Flip the **Read-only mode** toggle off
- Set **default_calendar** to one of the allowed calendars (the editor saves this as part of the standard schema fields)

Save. Every change is audited at `elevated` severity.

### 4. Plugin allowlist

Add `calendar` to the agent's `plugins.allow` (Security Center → Plugins).

## Provider-specific notes

### iCloud

- Base URL: `https://caldav.icloud.com`
- Username: the iCloud email address
- App password from appleid.apple.com (Apple requires 2FA to be on)
- iCloud's CalDAV is sometimes slow on first contact (cold cache); the 30-second read timeout default is enough

### Google

- Base URL: `https://www.google.com/calendar/dav/`
- 2-step verification must be on, then generate an App Password under "Security → App passwords"
- Google's CalDAV implementation is slightly non-standard; the `caldav` library handles the quirks

### Outlook / Office 365

- Base URL: `https://outlook.office365.com/owa/`
- Personal Microsoft accounts and Microsoft 365 business accounts have different paths; check the account's CalDAV docs

### Nextcloud

- Base URL: `https://<your-nextcloud>/remote.php/dav/`
- App password from Settings → Security → Devices & sessions
- If your Nextcloud is on RFC1918, also add an internal-IP grant in Security Center → Network

## Failure surface

| Refusal | Code | Inspector deep-link |
|---|---|---|
| 401 (token rejected) | `SPK_E_SECRET_NOT_FOUND` | `/secrets` |
| Calendar not in `allowed_calendars` | `SPK_E_PERMISSION_MISSING` | `/plugins?prefill=…` — editor opens with the matching calendar checkbox flashed + ticked |
| `read_only=true` blocked write | `SPK_E_PERMISSION_MISSING` | `/plugins?prefill=…` — editor opens with `read_only` toggle flashed + flipped off |
| Connect refused on RFC1918 | `SPK_E_URL_PRIVATE_IP` | `/security?tab=network` (existing prefill) |
| Host not in allow_hosts | `SPK_E_URL_DENIED` | Same |

## Recipes

### "What's on my calendar tomorrow?"

`read_only=true`, `allowed_calendars` includes the personal calendar. Agent calls `list_events` with tomorrow's date range; returns a trimmed list.

### "Find me a free hour between 2 and 5pm"

`find_free_slots` with `window_start=2026-05-09T14:00:00Z`, `window_end=2026-05-09T17:00:00Z`, `duration_minutes=60`. Returns gaps where no busy event exists across every allowed calendar.

### "Schedule lunch with Bob on Friday at noon"

Requires `read_only=false` + `default_calendar` set. First call refuses with `SPK_E_PERMISSION_MISSING (missing_toggle=read_only)`; Failure Inspector deep-links to the editor → flip read_only → save → retry.

## Sensitivity & redaction

Event titles, locations, and attendees flow through the existing redaction chain. Presidio catches `LOCATION` / `EMAIL_ADDRESS` / `PERSON` entities; the model sees `[REDACTED:LOCATION]` for sensitive places, not the raw address. Operators who want raw locations through can adjust per-agent policy on `pii.basic` in the Filtering page.

## Audit story

- Reads: standard tool-call audit row at `info`.
- Discovery: `kind=security.plugin.discover` at `info` with `{ok, error_code}` summary.
- Plugin config update: `kind=security.plugin_config.update` at `elevated` with the diff.
- `create_event` / `delete_event`: standard tool-call row + the regular guardrail policy audits.

## Out of scope

- **Recurring event expansion** — CalDAV servers do this server-side via `expand=True`; the plugin reads pre-expanded instances.
- **Free/busy with attendees** — getting free/busy across a colleague's calendar requires server-side delegation that CalDAV doesn't standardize cleanly across providers.
- **Reminders / VTODO** — separate plugin (`todo`) in the roadmap.
- **Multi-instance CalDAV** — one server per config. Run two instances of the plugin for two accounts.

## Source

- Plugin: [`spark/plugins/builtins/calendar.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/builtins/calendar.py)
- Discover route: `POST /api/plugin-config/calendar/discover`
- Editor: [`spark/web/frontend/src/components/CalendarConfigEditor.tsx`](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/components/CalendarConfigEditor.tsx)
- Tests: [`tests/unit/test_calendar_plugin.py`](https://github.com/Veilfire/EmberSpark/blob/main/tests/unit/test_calendar_plugin.py)
