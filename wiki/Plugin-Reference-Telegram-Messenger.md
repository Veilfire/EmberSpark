# Plugin: telegram_messenger (outbound)

| | |
|---|---|
| Direction | Outbound — agent → Telegram |
| Auth | Bot token from age vault |
| Permissions | `net.http`, `secrets.read` |
| Sensitivity | moderate |
| Network | yes |

Sends messages, edits them, posts inline-keyboard buttons, sets the
bot's command list. Pairs with the inbound bot runner
(`spark/scheduler/events/telegram_bot.py`) so a Telegram chat becomes
fully bidirectional.

## Operator config

```yaml
plugins:
  telegram_messenger:
    bot_token_secret: telegram_bot_token   # secret name in the age vault
    allow_chat_ids:                        # whitelist of chats the agent may send to
      - 123456789
      - -987654321
    parse_mode_default: MarkdownV2         # or HTML, plain
    timeout_seconds: 15.0
```

Set the bot token first:

```bash
spark secrets set telegram_bot_token   # paste the token from @BotFather
```

The model can never widen `allow_chat_ids`. Each `send_message` /
`edit_message` / `delete_message` / `send_chat_action` call is checked
against the list at runtime; cross-chat sends raise `PermissionError`.

## Actions

The plugin is action-discriminated. Pick one per call:

### `send_message`

```json
{
  "tool": "telegram_messenger",
  "args": {
    "action": "send_message",
    "chat_id": 123456789,
    "text": "✅ Run completed.\n\nResult: see deliverables.",
    "reply_to_message_id": 42,
    "inline_keyboard": [
      [{"text": "Open run", "url": "https://spark.local/runs/abc/replay"}],
      [{"text": "Run again", "callback_data": "rerun:fact-checker"}]
    ]
  }
}
```

Returns `{ok, message_id, message_ids}`. Long text is auto-split on
paragraph boundaries to fit Telegram's 4096-char limit; `message_ids`
gives every chunk's ID for follow-up edits.

### `edit_message`

```json
{
  "action": "edit_message",
  "chat_id": 123456789,
  "message_id": 8801,
  "text": "_running… 30%_"
}
```

Best paired with `send_message` to give a "thinking…" placeholder that
gets edited as progress comes in.

### `delete_message`

```json
{ "action": "delete_message", "chat_id": 123, "message_id": 8801 }
```

### `send_chat_action`

Sends a transient indicator like `typing` or `upload_photo` — Telegram
shows it for ~5 seconds.

```json
{ "action": "send_chat_action", "chat_id": 123, "chat_action": "typing" }
```

### `answer_callback`

Required after handling an inline-keyboard button press so Telegram
stops the loading spinner.

```json
{
  "action": "answer_callback",
  "callback_query_id": "291348190291",
  "text": "Approved.",
  "show_alert": false
}
```

### `set_commands`

Publishes the bot's command list so users see autocomplete in the
message bar. Idempotent — the bot runner calls this at startup
automatically; the agent generally doesn't need to.

```json
{
  "action": "set_commands",
  "commands": [
    {"command": "help", "description": "Show help"},
    {"command": "review", "description": "Review a PR by number"}
  ]
}
```

## Threat model

- **Token in vault** — bot token is read from the age vault by name.
  The model never sees the cleartext.
- **Chat allowlist** — operator-locked. Cross-chat sends rejected at
  the plugin boundary.
- **No file uploads** — sendPhoto / sendDocument not exposed. Use
  `webhook` or upload elsewhere and link back.
- **No `forwardMessage` / `copyMessage`** — agent can't relay messages
  between chats.
- **Header sanitization** — all calls go through Telegram's typed Bot
  API; raw HTTP not exposed.

## Related

- [Telegram Bot Setup Guide](Telegram-Bot-Setup) — full setup, including
  how the inbound bot runner uses this plugin to reply.
- [Scheduling Guide § Telegram bot trigger](Scheduling-Guide#recipe-telegram-bot-trigger)
- [Concept: Permissions](Concepts-Permissions)
