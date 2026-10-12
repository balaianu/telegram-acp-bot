# How To Use The Bot From Telegram

This guide shows the main command flow for day-to-day usage.

## 1. Start working (implicit session)

Send a normal prompt. If the chat has no active session, the bot creates one automatically in the default workspace.

Example:

```text
Please review the latest commit and suggest a fix.
```

You can also attach images/documents; the bot forwards them to ACP prompt input.

## 2. Start or switch sessions with `/new`

Create a session in the default workspace:

```text
/new
```

Create a session in a specific workspace:

```text
/new /home/user/project
```

The bot will reply with the ACP `session_id` and workspace path.

Use `/new` when you want a different session/workspace than the current one.

## 3. Resume an existing ACP session

List resumable sessions and pick one from buttons:

```text
/resume
```

Resume by list index (0-based, where `0` is the most recent):

```text
/resume 0
```

List resumable sessions for a specific workspace:

```text
/resume /home/user/project
```

This resumes the most recent session in that workspace.

When you tap a button, the bot calls ACP `session/load` and switches the active conversation for that chat.

`/resume` accepts either an index or a workspace path (not both in the same command).

If your agent does not support ACP `session/list`, `/resume` will report it.

## 4. Inspect current session

```text
/session
```

Shows the active session workspace.

## 5. Cancel, stop, and clear

Cancel current in-flight operation:

```text
/cancel
```

Stop current session process:

```text
/stop
```

Clear current session:

```text
/clear
```

## 6. Switch activity mode

Show the current mode:

```text
/mode
```

Set the mode explicitly:

```text
/mode normal
/mode compact
/mode verbose
```

The available modes are:

- `normal`: separate activity messages, no streaming edits.
- `compact`: one in-progress status message that names the current action and becomes the final answer.
- `verbose`: in-place append-only streaming for reply text and active tool output.

(how-to-busy-queue)=
## 7. Busy-state handling

When you send a message while the agent is already processing a previous prompt, the bot:

- Queues your message automatically.
- Replies to that queued message with the busy notice.
- Shows a temporary **Send now** inline button.

### Send now

Pressing **Send now** immediately cancels the current in-flight operation and processes your queued message.
The queued notice is updated to `✅ Sent.` so chat state matches what happened.

If the current task finishes naturally before you press the button, your queued message runs automatically and the button is removed (pressing it after that shows "Already sent." with no side effects).

## 8. Schedule a prompt directly

You can schedule a deferred agent prompt without going through the agent, using `/schedule`. This is useful when the model rejects your request due to quota limits or when you want to queue work for later without starting an agent session first.

```text
/schedule <time> <prompt text>
```

Supported time formats:

- `30s` — 30 seconds from now
- `10m` — 10 minutes from now
- `2h` — 2 hours from now
- `1d` — 1 day from now
- Natural-language dates such as `tomorrow 9am` or `mañana 9am`
- ISO timestamp with timezone, e.g. `2026-04-11T10:00:00+00:00`

Example:

```text
/schedule tomorrow 9am Check for new review comments on the open PR
Summarize what changed
Flag anything blocking merge

/schedule mañana 9am Generate the weekly summary report
Include pending reviews
Mention overdue follow-ups

/schedule 2026-04-12T09:00:00+00:00 Generate the weekly summary report
Include pending reviews
Mention overdue follow-ups
```

The bot replies with the scheduled execution time. When the time arrives, the stored prompt is sent to the agent and the reply is posted back to the chat as a reply to your `/schedule` message.

The prompt may also continue on following lines. Everything after the time spec is stored verbatim, so multiline prompts keep their line breaks.

Natural-language parsing uses English and Spanish by default. You can customize the accepted languages with {term}`ACP_SCHEDULE_LANGUAGES` or `telegram.schedule_languages` in the config file.

Single-line prompts still work too:

```text
/schedule 2h Generate the weekly summary report
```

If no active session exists when the scheduled time arrives, the bot will report that the task could not be run automatically.

## 9. Inspect and cancel scheduled follow-ups

When the agent schedules a deferred follow-up, you can inspect the pending work directly from Telegram:

```text
/scheduled
```

The bot replies with a compact summary of the scheduled tasks for the current chat. Pending tasks are shown with inline **Cancel** buttons, so you do not have to copy or type task ids manually.

A typical interaction looks like this:

```text
/scheduled

Scheduled tasks for this chat:

Pending:
1. prompt_agent | 2026-03-30 22:10:00 UTC
  Check the PR again and report any new comments.
2. notify | 2026-03-30 22:30:00 UTC
  Remind me to restart the bot.
```

From there you can tap **Cancel 1**, **Cancel 2**, and so on, or use **Cancel all pending** if you want to clear the queue for that chat.

For this first version, cancellation is intentionally limited to tasks that are still `pending`. Tasks that are already `running` are visible for inspection, but they are not turned into interruptible jobs by this command.

## 10. Restart bot process (dev workflow)

```text
/restart
```

With an active session, the bot replies with:

```text
Restart requested. Re-launching process...
Session restarted: <session_id> in <workspace>
```

Then it exits polling and re-execs the process (or uses `ACP_RESTART_COMMAND` if configured).

If there is no active session, it replies:

```text
No active session. Use /new first.
```

Resume a specific saved session in-process with the restart command:

```text
/restart 0
```

You can combine index and workspace filter:

```text
/restart 2 /home/user/project
```

That path replies with:

```text
Session resumed: <session_id> in <workspace>
```

## 11. Run the agent's own slash commands

Some agents publish their own slash commands over ACP. Ask for them from Telegram:

```text
/cmds
```

The answer is a keyboard with one button per advertised command, three to a row, plus a last button that prints the
same commands as text with their description and input hint:

```text
/goal: Set or manage a persistent goal — <objective> [--turns <number>] | status | clear
/deep-research: Fan out web research and return a cited report
```

Tapping a button runs that command in the active session with no arguments. Commands that need input still work,
because the agent answers with its own usage text; type the command with its arguments once you know them, which is
matched in message text and in photo or document captions:

```text
/goal status
```

Typing and tapping share the rest of the rules: the allowlist, the group @mention gate, and the busy queue all still
apply. An unmentioned group `/command` is ignored in silence, so the bot does not answer with the unknown-command hint
either. Tapping a button counts as addressing the bot, so a tap runs in a group without an @mention. When Telegram adds
the bot's own `@username` to a command picked from the native menu, the bot strips that mention before forwarding, so
the agent receives `/goal status`.

The keyboard is bounded: `AGENT_COMMAND_KEYBOARD_MAX_ROWS` rows of `AGENT_COMMAND_KEYBOARD_COLUMNS` buttons are shown
and the remaining commands stay available through the listing button and by typing. A command whose name is not built
from `a-z`, `0-9`, `_` or `-` gets no button, because its name cannot travel as button data.

Three limits to know about:

- The list is empty until the agent advertises its commands, which normally happens when the session is created. Send
  a message or use `/new` first, then ask again.
- The bot's own commands always win. `/help`, `/mode` and the rest belong to the bot even if the agent advertises a
  command with the same name. Telegram only recognises a command in message text, so a bot command written in a media
  caption is dispatched by this gate and still runs the bot's handler without its arguments.
- The agent's commands never join Telegram's native `/` menu, which only mirrors the bot's own command list. Type them
  or copy them from `/cmds`. That menu comes from `register-commands`, so after an upgrade that adds a bot command,
  run `telegram-acp-bot register-commands --telegram-token "$TELEGRAM_BOT_TOKEN"` again or the new entry stays hidden.

A command the agent did not advertise is rejected instead of forwarded, so a typo never reaches the agent as prompt
text, and a button left over from an older keyboard reports that the command is no longer advertised rather than running
it. Every advertisement replaces the previous one, so an agent that gains commands mid-session shows them the next time
you ask for `/cmds`. Commands that need an interactive terminal answer that themselves, for example
`Command /help requires interactive UI and is not available in ACP.`

## 12. Answer a question the agent asks you

When the agent needs a decision it asks over ACP as an *elicitation*: a form whose fields each list the answers it will
accept. The bot turns that form into a keyboard in the chat the question came from:

```text
❓ The agent is asking

Answer Questions

Báo cáo theo

[ Theo danh mục ]  [ Theo tháng ]
[ ⏭️ Skip this question ]
```

Tapping an option sends that answer to the agent and the prompt continues from it. A question with several answers
(multi-select) marks each pick with `✓` as you tap and waits for `✅ Send my answer`, so one response can carry several
options. `⏭️ Skip this question` declines the elicitation, which lets the agent proceed without an answer instead of
waiting. A form of two questions arrives as one message listing both prompts and offering both sets of buttons; the
agent is answered only once every question has a choice.
Once the answer reaches the agent the buttons are removed from the question message, so the same question cannot be
answered twice and the message is left as a plain record of what was asked. A question the agent stops waiting for
loses its buttons the same way, even if nobody answered it: as soon as the agent's turn ends, a question that is still
open is closed, and the five-minute deadline is only the backstop for a turn that never ends at all. A question still
showing buttons is therefore the one still waiting, so always tap the newest keyboard rather than an older one left on
screen.

Three limits to know about:

- Only fields the agent offered as a choice of known options become buttons. A field you would have to type into, or an
  elicitation that is not a form, is declined so the agent falls back to asking in plain text. The same fallback happens
  when Telegram refuses the question message itself (for example under flood control): no keyboard is left half-shown,
  and the agent is told at once that the buttons did not arrive.
- The question waits five minutes for a tap and is then cancelled, and the last `ELICITATION_FORMS_MAX` forms are
  remembered. When a new question pushes an older one out of that list, the older question is declined so the agent stops
  waiting for it and its buttons are taken off the message — a keyboard never outlives the answer it is asking for.
  A button from an older keyboard, or one whose question the agent already stopped waiting for, answers with
  a toast instead of being sent twice.
- A button shows at most `ELICITATION_OPTION_LABEL_CHARS` characters of the option and ends in `…` when the agent offered
  a longer one, so a cut label is never mistaken for a complete answer. The question line above still shows what the
  agent asked, but the rest of a cut option is only visible if the agent repeats it there, so an option meant to be
  chosen by tap should be short.

The question is sent as its own message rather than into the compact status bubble, because the status line is rewritten
while the agent works and would overwrite a question nobody has answered yet.

## Notes

- Session scope is per Telegram chat.
- The first prompt creates a session implicitly when none is active (including after `/restart`).
- `/new` replaces the active session for that chat and is intended for explicitly switching to another workspace/session.
- `/resume` is ACP-native (`session/list` + `session/load`) and depends on agent capabilities.
- `/mode` is stored per Telegram chat, so different chats can use different activity modes.
- `/cmds` shows the slash commands the agent advertised for the current session as tap-to-run buttons; see the how-to
  section above.
- A question the agent asks over ACP (an elicitation) is offered as tap-to-answer buttons; see the how-to section above.

## Logging and traceability

Every application log line includes contextual identifiers:

- `chat_id`: Telegram chat id.
- `session_id`: ACP session id (when a session is active).
- `prompt_cycle_id`: per-prompt cycle/task id generated for each incoming user prompt.

This lets you trace:

- all activity from one ACP session (`session_id`),
- all logs tied to a single prompt execution (including queued prompts) via `prompt_cycle_id`.

Use {term}`ACP_LOG_FORMAT`=`json` to emit structured logs for log aggregators.

By default, the bot also emits compact text previews for prompts and replies:

- `Prompt received: ...`
- `Reply sent: ...`

The human-oriented `text` log format uses `rich` output in the terminal to highlight the logger, chat/session/cycle ids, and the message preview.

To keep logs focused on auditability, verbose transport/framework logs (for example `httpx` Telegram API request lines) are downgraded to warning level by default.
