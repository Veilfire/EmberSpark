# Telegram Bot Setup

This page walks through turning EmberSpark into a Telegram chatbot —
users DM (or @-mention in groups), the bound agent thinks, and the bot
replies. Like a chat app, but with EmberSpark's bounded-autonomy +
budgets behind it.

## What you get

- **Bidirectional chat**: send a message, get a reply.
- **Slash commands**: built-in `/help`, `/runs`, `/run`, `/cancel`,
  `/whoami`, plus operator-defined commands routed to specific tasks.
- **Per-user authorization**: chat_id whitelisting + per-user_id allow
  lists so a public group can be safe.
- **Multi-chat / multi-agent**: one bot can serve different agents in
  different chats — `research-assistant` in DMs, `code-reviewer` in
  the engineering group, etc.
- **Long-op UX**: typing indicator while the task runs, `_thinking…_`
  placeholder, edit-the-message progress, final response replaces the
  placeholder.
- **HITL groundwork**: inline-keyboard button presses route through
  the bot runner so future approval / cancel buttons just plug in.

## Setup overview

1. Create a bot with `@BotFather`. Save the token.
2. Store the token in the age vault: `spark secrets set telegram_bot_token`.
3. Allowlist the inbound bot's chats and the outbound messenger's chats
   (same list — symmetric).
4. Define a task with `mode: event` and `on: { type: telegram_bot, ... }`.
5. Restart the server. The bot starts long-polling and serves traffic.

## Step 1 — BotFather

In Telegram, message `@BotFather`, send `/newbot`, follow the prompts.
Save the token (looks like `123456:ABC-DEF…`). You can also `/setprivacy`
→ Disable to let the bot read non-mention messages in groups.

## Step 2 — Vault the token

```bash
spark secrets set telegram_bot_token
# paste the token from BotFather, no quotes
```

## Step 3 — Find your chat IDs

Easiest way: send the bot a message in the chat you want to bind, then
hit `https://api.telegram.org/bot<TOKEN>/getUpdates` once. Find the
`chat.id`. DMs are positive; groups are negative; supergroups are
larger negatives starting with `-100`.

A throwaway helper:

```bash
TOKEN=$(spark secrets get telegram_bot_token)
curl -s "https://api.telegram.org/bot$TOKEN/getUpdates" | jq '.result[].message.chat.id'
```

## Step 4 — Configure the agent (outbound side)

Add `telegram_messenger` to the agent's plugin allowlist + grants and
configure it. The operator owns the chat allowlist; the agent can't
widen it.

```yaml
# agents/research-assistant.yaml
spec:
  plugins:
    allow:
      - telegram_messenger
      - web_search
      - markdown_writer
  permissions:
    grants:
      - net.http
      - secrets.read
  required_secrets:
    - telegram_bot_token
```

In **Plugins → telegram_messenger** in the web UI (or `~/.spark/plugin_config.yaml`):

```yaml
telegram_messenger:
  bot_token_secret: telegram_bot_token
  allow_chat_ids:
    - 123456789       # DM with the operator
    - -1001234567890  # engineering supergroup
  parse_mode_default: MarkdownV2
```

## Step 5 — Configure the bot runner (inbound side)

Create a task with `mode: event` and the new `telegram_bot` event. This
is the chat-listening side.

```yaml
# tasks/telegram-bot.yaml
apiVersion: spark.veilfire.dev/v1alpha1
kind: Task
metadata:
  name: telegram-bot
spec:
  agent: research-assistant      # default agent for the conversational fallback
  mode: event
  on:
    type: telegram_bot
    bot_token_secret: telegram_bot_token

    bindings:
      # 1:1 DM with the operator — full conversational mode + /run + /cancel.
      - chat_id: 123456789
        agent: research-assistant
        allow_user_ids: [42]                       # the operator's Telegram user_id
        mode: conversational
        allow_run_tasks: [weekly-digest, fact-check]   # explicit /run allowlist
        allow_cancel: true                          # opt in to /cancel <run_id>

      # Engineering group — command-only mode, restricted to a few engineers.
      # No /run, no /cancel — built-in chat commands only.
      - chat_id: -1001234567890
        agent: code-reviewer
        allow_user_ids: [42, 99, 7]
        mode: command_only

    commands:
      - command: review
        description: Review a PR by number
        action: run_task
        task: code-review-on-merge
      - command: digest
        description: Run the weekly digest
        action: run_task
        task: weekly-research-digest

    poll_seconds: 10
    long_poll_timeout: 25
    typing_indicator: true

  objective: >
    Respond to Telegram messages from approved chats. Use
    telegram_messenger to send replies; use the trigger payload to
    understand who sent the message and what they asked.
```

## How a conversation flows

1. User in chat 123456789 sends `What papers came out today on RLHF?`.
2. Bot runner checks: chat_id ∈ bindings ✓, user_id ∈ allow_user_ids
   ✓, mode is conversational, not a command.
3. Bot sends a typing indicator and a placeholder `_thinking…_`.
4. Bot fires `execute_task_by_name("telegram-bot", payload={...})`.
   The task's bound agent is `research-assistant`. The
   trigger_payload contains `{chat_id, user_id, user_name,
   message_id, text, placeholder_message_id}`.
5. `research-assistant` plans, calls `web_search`, drafts a reply,
   then calls `telegram_messenger` with `action: edit_message,
   chat_id: 123456789, message_id: <placeholder>` to replace the
   placeholder with the final answer.

The placeholder is the recommended UX for a chatbot — fewer message
notifications than "running…" + final-answer-as-new-message, and the
final result lands where the user expects.

## How `/run` works

```
> /run weekly-research-digest "site:arxiv.org RLHF"
```

The bot parses `/run`, checks the binding's `allow_run_tasks` list,
fires `weekly-research-digest` (not the bot task) with
`payload = {command: "run", args: ["site:arxiv.org RLHF"], ...}`.
The fired task reads `args` from `state.trigger_payload`. Every
`/run` invocation writes an `elevated`-severity audit row
(`telegram.run`) tagged with the user_id and chat_id.

**Safe by default.** A binding with no `allow_run_tasks` refuses
`/run` entirely. Operators must explicitly allowlist tasks per
binding. This prevents a user with a chat seat from firing arbitrary
tasks they shouldn't have access to.

## How `/help`, `/runs`, `/cancel`, `/whoami` work

Built-ins. The bot runner handles them directly — no task fires:

- `/help` — assembles a list of built-in + custom commands.
- `/runs` — reads the last 5 runs of the bound agent and posts a
  formatted summary.
- `/cancel <run_id>` — flips the run row's state to `stopped`.
  **Disabled by default**; opt in with `allow_cancel: true` on the
  binding. Even when enabled, cancel only works for runs whose
  `agent_name` matches the binding's `agent` — a chat bound to
  `research-assistant` cannot cancel a `code-reviewer` run.
  Every cancel is audited (`telegram.cancel`, `elevated`).
- `/whoami` — replies with the user's chat_id, user_id, and the
  binding info.

## Group safety

In a Telegram group, **always set `allow_user_ids`**. Empty list means
"any group member can talk to the bot." If the group is open or
invitable, that's a footgun.

For supergroups, also `/setprivacy` → Disable in BotFather, otherwise
the bot only sees messages that @-mention it or reply to its
messages.

## Feature matrix

| Feature | Status |
|---|---|
| Long-poll inbound (no public URL) | ✅ |
| Per-chat agent binding | ✅ |
| Per-user authorization | ✅ |
| Built-in `/help`, `/runs`, `/run`, `/cancel`, `/whoami` | ✅ |
| Custom slash commands → task fires | ✅ |
| Conversational mode with placeholder editing | ✅ |
| Typing indicator | ✅ |
| Auto-split long messages | ✅ |
| Inline keyboards (URL + callback buttons) | ✅ |
| Callback query handling (button presses) | ✅ ack only — full HITL is follow-up |
| BotCommands autocomplete | ✅ (auto-published at startup) |
| Photo / file send | ❌ — use `webhook` to push artifacts elsewhere |
| Voice / audio | ❌ |
| Telegram Mini Apps | ❌ |

## Threat model

- **Token vault-only** — the bot token never leaves the age vault.
  Listed in `agents/<name>.yaml` `required_secrets:`. Tokens are
  classified by the privacy redactor (`TELEGRAM_BOT_TOKEN`) so they
  never appear in plain in any log.
- **Three-tier auth**:
  1. `chat_id` must be in `bindings`.
  2. `user_id` must be in the binding's `allow_user_ids` (when set).
  3. `/run` is gated by an explicit per-binding `allow_run_tasks`
     allowlist (empty = disabled).
- **`/cancel` is opt-in** — `allow_cancel: false` by default, and
  even when enabled it only stops runs of the binding's bound agent.
- **Audited at `elevated`** — every `/run` and `/cancel` writes an
  audit row tagged with the Telegram user_id, chat_id, and the
  affected target.
- **Sandboxed by construction** — every task fired by the bot runs
  under the agent's normal budget / permission gates. A bot user
  can't escalate beyond what the agent could already do.
- **No webhook URL** — long-poll means no public callback. Works
  behind NAT.
- **Replay-safe** — Telegram's `update_id` offset semantics mean
  duplicate updates can't replay tasks.

## Troubleshooting

**Bot doesn't respond.** Check the server logs for `telegram_bot.poll_failed`,
`telegram_bot.token_missing`, or `telegram_bot.chat_blocked`. Most
commonly: the chat_id isn't in `bindings`, or the user_id isn't in
`allow_user_ids`.

**Bot can't reply.** The inbound bot runner reads inbound messages, but
replies go through the `telegram_messenger` plugin. Confirm:

- Plugin `allow_chat_ids` includes the chat.
- Agent's allowlist includes `telegram_messenger`.
- Agent's `required_secrets` includes `telegram_bot_token`.

**`/run` says "unknown task".** The task in `tokens[0]` must already be
registered. Run `spark task list` (or check the Scheduler page) to
confirm.

**Markdown rendering looks broken.** Telegram is strict about
MarkdownV2 escaping. Set `parse_mode_default: HTML` if you'd rather
emit HTML, or `plain` to disable formatting.

## Related

- [Plugin: telegram_messenger](Plugin-Reference-Telegram-Messenger)
- [Scheduling Guide § Telegram bot trigger](Scheduling-Guide#recipe-telegram-bot-trigger)
- [Permissions Guide](Permissions-Guide)
