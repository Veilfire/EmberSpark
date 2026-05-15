# Plugin: `slack`

Post messages, react, and (with a user token) search Slack. No
`slack_sdk` dep — ~250 lines of httpx wrapping the half-dozen Slack
Web API endpoints we actually need.

| | |
|---|---|
| **Required permissions** | `NET_HTTP`, `SECRETS_READ` |
| **Sensitivity** | `MODERATE` |
| **Network** | Yes (slack.com) |
| **Output filtered** | Yes |

## Actions

| Action | Slack API | Required config |
|---|---|---|
| `list_channels` | `conversations.list` | none |
| `list_users` | `users.list` | none |
| `post_message` | `chat.postMessage` | `channel` in `allow_channel_ids` (or `allow_dm_user_ids` for DM target) |
| `update_message` | `chat.update` | same |
| `react` | `reactions.add` | same |
| `search_messages` | `search.messages` | `user_token_secret` set + populated |

## Bootstrap

1. **Create a Slack app** at api.slack.com/apps; add bot scopes:
   `chat:write`, `chat:write.public`, `reactions:write`,
   `channels:read`, `groups:read`, `im:write`, `users:read`. Install
   to your workspace.
2. **Copy the bot token** (`xoxb-…`). For search, also generate a
   user token from the same app (separate scope: `search:read`).
3. `spark secrets set slack_bot_token`
4. (Optional, for search) `spark secrets set slack_user_token` and
   set `user_token_secret = slack_user_token` in plugin config.
5. **Invite the bot to channels** it should post to — Slack only shows
   the bot channels it's a member of; non-member channels are
   filtered from discovery.
6. **Plugins page** → slack → **Test connection & discover**. Two
   checkbox grids appear:
   - **Allowed channels** — pick channels. `#general` /
     `#announcements` carry an elevated chip; private channels too.
   - **Allowed DM recipients** — pick users. Admins / owners carry a
     **danger** chip and require typed-confirm.

## Failure surface

| Refusal | Code | Inspector deep-link |
|---|---|---|
| 401 / `invalid_auth` / `token_revoked` | `SPK_E_SECRET_NOT_FOUND` | `/secrets` |
| Channel not in `allow_channel_ids` | `SPK_E_PERMISSION_MISSING` | `/plugins?prefill=…` — channel checkbox flashed + ticked |
| User not in `allow_dm_user_ids` | `SPK_E_PERMISSION_MISSING` | `/plugins?prefill=…` — DM user checkbox flashed + ticked |
| `search_messages` without user_token_secret | `SPK_E_OPERATOR_OVERRIDE_REFUSED` | Inline message (operator must add a user token) |

## Source

- Plugin: [`spark/plugins/builtins/slack.py`](https://github.com/Veilfire/EmberSpark/blob/main/spark/plugins/builtins/slack.py)
- Discover route: `POST /api/plugin-config/slack/discover`
- Editor: [`spark/web/frontend/src/components/SlackConfigEditor.tsx`](https://github.com/Veilfire/EmberSpark/blob/main/spark/web/frontend/src/components/SlackConfigEditor.tsx)
- Tests: [`tests/unit/test_slack_plugin.py`](https://github.com/Veilfire/EmberSpark/blob/main/tests/unit/test_slack_plugin.py)
