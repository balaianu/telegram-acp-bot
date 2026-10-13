# Configuration

telegram-acp-bot uses a three-layer configuration model. Settings are resolved in this order:

```
CLI flags  >  Environment variables  >  Config file  >  Built-in defaults
```

You can mix layers freely. A typical setup uses a config file for static values and environment variables for secrets that should not live on disk.

(config-file)=
## Config file

The bot loads configuration from the following locations (in order):

1. `--config PATH`, or {term}`ACP_CONFIG_FILE` when the flag is absent
2. `./.telegram_acp_bot/config.json` (project-local)
3. `{envvar}`XDG_CONFIG_HOME/telegram_acp_bot/config.json` (or `~/.config/telegram_acp_bot/config.json` when unset)
4. `~/.telegram_acp_bot/config.json` (legacy)

The first readable file wins. A pinned path is used as given: when `--config` or
{term}`ACP_CONFIG_FILE` names a file that cannot be read, the bot stops with that path in
the message instead of quietly falling back to discovery. Pinning is what lets a deployment
keep config, state and logs in one directory while a shell and a service manager still read
the same file.

```bash
# Automatic discovery (no --config needed if the file is in a standard location)
telegram-acp-bot

# Explicit path (overrides automatic discovery)
telegram-acp-bot --config ./telegram-acp-bot.json

# Same effect for every call in this shell, including sub-commands
export ACP_CONFIG_FILE=$HOME/.acp/config.json
telegram-acp-bot register-commands --dry-run
```

The file must be a JSON object. All keys are optional. Missing keys fall back to environment variables or built-in defaults.

### Schema

```json
{
  "telegram": {
    "bot_token": "123456:abc",
    "allowed_user_ids": [123456789],
    "allowed_usernames": ["alice", "@bob"],
    "schedule_languages": ["en", "es"],
    "group_require_mention": true,
    "group_media_queue": true,
    "status_reactions": "minimal"
  },
  "acp": {
    "agent_command": "npx @zed-industries/codex-acp",
    "restart_command": "uv run telegram-acp-bot --config ./telegram-acp-bot.json",
    "permission_mode": "ask",
    "permission_event_output": "stdout",
    "stdio_limit": 8388608,
    "connect_timeout": 30,
    "log_format": "text",
    "log_level": "INFO",
    "activity_mode": "normal",
    "scheduled_tasks_db": "/home/alice/.local/state/telegram-acp-bot/scheduled-tasks.sqlite3",
    "workspace": "/home/alice/projects"
  },
  "agents": {
    "qoder": {
      "acp": {
        "agent_command": "qodercli --acp"
      }
    },
    "codex": {
      "telegram": {
        "bot_token": "987654:xyz",
        "allowed_user_ids": [987654321]
      },
      "acp": {
        "agent_command": "npx @zed-industries/codex-acp"
      },
      "mcp_servers": {
        "context7": {
          "url": "https://mcp.context7.com/mcp"
        }
      }
    }
  }
}
```

Without `agents`, the flat `telegram`/`acp` sections describe one bot, which is all most
setups need. With `agents`, the process runs one bot per entry; see {ref}`config-agents`.

### Validation

The config file is validated at startup. Invalid values (wrong types, unknown enum values) are reported as clear error messages and the bot does not start.

(config-agents)=
## Multiple agents

One process can run several agents side by side: each declared agent gets its own ACP
child process and its own Telegram bot, all polled from a single event loop. Everything
below describes that fleet; a config without `agents` runs exactly one bot named
`default` and behaves as it always did.

### Names

An agent name must match `^[a-z0-9][a-z0-9_-]{0,62}$`, because the same string is a log
field, an environment variable suffix and a directory name. Hyphens are legal in a name
and illegal in a variable, so they become underscores when the name is used as a suffix:
agent `code-review` is pinned by a `TELEGRAM_BOT_TOKEN_CODE_REVIEW` environment variable.

### What an agent may override

Each `agents.<name>` entry may only repeat the top-level sections `telegram`, `acp` and
`mcp_servers`, so one syntax describes a single bot and a fleet alike. The flat sections
are the base and each agent layers its own keys on top, key by key. `mcp_servers` is
merged by server name and the agent's definition wins, so one agent can swap a shared
server without changing it for the others.

### Tokens

Every bot needs a distinct Telegram token, resolved in this order:

1. `TELEGRAM_BOT_TOKEN_<NAME>` in the environment.
2. `agents.<name>.telegram.bot_token` in the config file.
3. The flat token (`--telegram-token`, {term}`TELEGRAM_BOT_TOKEN` or `telegram.bot_token`).

Startup refuses two agents that end up on the same token and names both, because Telegram
answers concurrent polls of one bot with error 409 — the fleet would look alive and lose
updates.

The flat sections still need a value, because they are what an unset key falls back to: the
base carries `telegram.bot_token` and `acp.agent_command` (or their environment
equivalents) alongside the per-agent pins that make each bot distinct. An agent that ends
up on the base token is reported by the same duplicate-token check.

### Declaring agents on the command line

- `--agent NAME=COMMAND` declares an agent, or overrides the command of a declared one,
  without editing the config file. It is repeatable. A name that only appears here still
  needs a token from one of the sources above.
- `--only NAME` runs a subset of the declared agents. It is repeatable and refuses a name
  that nothing declares. The config file offers the same list under a top-level `"only"`
  key, which the flag replaces.

### Runtime files

Two bots must not route through one file, so a named agent keeps its channel state and
its scheduled-tasks database in a directory of its own under {envvar}`XDG_STATE_HOME`
(`~/.local/state/telegram-acp-bot/agents/<name>/` by default), pinned by
`ACP_TELEGRAM_CHANNEL_STATE_FILE_<NAME>` and `ACP_SCHEDULED_TASKS_DB_<NAME>`; both are
defined in the {ref}`env-var-glossary` below. Each agent also gets its own scheduler and
its own SQLite database, which is what stops one task from being claimed twice.

### Failure and restart

The fleet is all-or-nothing on purpose. If any agent fails to start, or fails to shut
down cleanly, the process stops every other bot and exits non-zero, so the service
manager restarts the whole thing. A half-alive fleet would leave one chat silently
answering nothing. `/restart` follows the same rule: it relaunches the process, and every
agent in it.

While it runs, every log line an instance produces carries an `agent` field, and `/help`
names the agent behind the bot whenever it is not `default`, so two bots in one process stay
tellable apart. To run two agents, follow {ref}`how-to-two-agents`.

(env-var-glossary)=
## Environment variables

Environment variables override any matching config file value. They are the recommended way to pass secrets like the bot token in container or CI environments.

```{glossary}
TELEGRAM_BOT_TOKEN
  Telegram bot token (from BotFather). Required unless set via config file or `--telegram-token`.

TELEGRAM_BOT_TOKEN_<NAME>
  Telegram bot token of one declared agent, where `<NAME>` is the agent name uppercased
  and with `-` replaced by `_`. Wins over `agents.<name>.telegram.bot_token` and over the
  flat {term}`TELEGRAM_BOT_TOKEN`, so a fleet keeps its secrets out of the config file.
  Two agents that resolve to the same token are rejected at startup.
  Example: `TELEGRAM_BOT_TOKEN_QODER`.

TELEGRAM_ALLOWED_USER_IDS
  Comma-separated allowlist of Telegram numeric user IDs.
  Example: `123456,987654`.

TELEGRAM_ALLOWED_USERNAMES
  Comma-separated allowlist of Telegram usernames.
  Usernames are normalized to lowercase and can include or omit `@`.
  Example: `alice,@bob`.

ACP_SCHEDULE_LANGUAGES
  Comma-separated language codes used when parsing natural-language `/schedule` times.
  Default: `en,es`.
  Example: `es,en`.

ACP_TELEGRAM_GROUP_REQUIRE_MENTION
  Truthy values (`1`, `true`, `yes`, `on`) restrict the bot to `@mentions` in group chats.
  Messages that neither mention the bot nor reply to one of its messages are ignored;
  private chats are always answered. Equivalent to `--group-require-mention`.
  Default: unset, so group chats answer every allowed sender.

ACP_TELEGRAM_GROUP_MEDIA_QUEUE
  Truthy values (`1`, `true`, `yes`, `on`) keep group media that the mention gate ignores instead of dropping it.
  Only the Telegram `file_id` is queued per chat; the next answered prompt lists those references and the agent
  downloads one with `telegram_fetch_attachment` when it actually needs it. Requires
  {term}`ACP_TELEGRAM_GROUP_REQUIRE_MENTION`, since without a gate every message is already answered.
  Default: unset, so unmentioned group media is discarded.

ACP_TELEGRAM_STATUS_REACTIONS
  Lifecycle emoji the bot puts on the prompt message itself, independent of what the agent chooses to do.
  Allowed values: `off`, `minimal`. `minimal` marks 👀 while the chat is busy, 🤔 while the agent works,
  then 👍 when the answer is delivered or 💔 when the cycle ends without one; each new reaction replaces
  the previous one on the same message.
  Default: `off`, so the bot adds no reactions and only the agent's `telegram_set_message_reaction` calls appear.

ACP_AGENT_COMMAND
  Command line used to launch the ACP agent process.
  Examples: `npx @zed-industries/codex-acp`, `uv run examples/echo_agent.py`.
  Required unless set via config file or `--agent-command`.

ACP_RESTART_COMMAND
  Optional command used by `/restart` to relaunch the bot process.
  Recommended when you run with `uv run ...` and need to preserve its flags.

ACP_PERMISSION_MODE
  Default permission policy for ACP tool calls.
  Allowed values: `ask`, `approve`, `deny`.

ACP_PERMISSION_EVENT_OUTPUT
  Permission/tool event log output mode.
  Allowed values: `stdout`, `off`.

ACP_STDIO_LIMIT
  Asyncio stdio reader limit in bytes for ACP transport.
  Increase this if the agent emits very large JSON lines.

ACP_CONNECT_TIMEOUT
  Timeout in seconds for ACP initialize/new_session handshake.

ACP_CONFIG_FILE
  Path to the config file, for every call that does not pass `--config`. Useful when a
  deployment keeps config, state and logs in one directory that is not a discovery
  location. A path that cannot be read is an error, not a reason to fall back to
  discovery. `--config` wins when both are present.
  Example: `ACP_CONFIG_FILE=/home/alice/.acp/config.json`.

ACP_LOG_LEVEL
  Application log level.
  Common values: `DEBUG`, `INFO`, `WARNING`, `ERROR`.

ACP_LOG_FORMAT
  Application log format.
  Allowed values: `text`, `json`.

ACP_ACTIVITY_MODE
  Controls how intermediate agent activity events are shown in Telegram.
  Allowed values: `normal`, `compact`, `verbose`.
  `normal` (default) emits each visible activity event as its own message.
  `compact` collapses all events into a single in-place status message that
  names the action in progress (command, or file relative to the workspace),
  repaints it at most once every few seconds to stay inside Telegram's per-chat
  message limit, and is replaced by the final answer when the agent responds.
  `verbose` streams append-only updates in place for active reply text and
  tool activity, and then finalizes those messages when the prompt completes.

ACP_SCHEDULED_TASKS_DB
  Path to the SQLite database used for deferred follow-up tasks.

ACP_SCHEDULED_TASKS_DB_<NAME>
  Path to the deferred follow-up database of one declared agent, `<NAME>` uppercased with
  `-` replaced by `_`. Wins over `agents.<name>.acp.scheduled_tasks_db`. Without either, a
  named agent gets its own database in the per-agent state directory, such as
  `~/.local/state/telegram-acp-bot/agents/qoder/scheduled-tasks.sqlite3`, so each bot runs
  its own scheduler on its own rows.
  Example: `ACP_SCHEDULED_TASKS_DB_QODER=/srv/qoder.sqlite3`.

ACP_TELEGRAM_CHANNEL_ALLOW_PATH
  Enables `path` inputs for the internal MCP `telegram_send_attachment` tool.
  Disabled by default. Set to `1` (or `true`/`yes`/`on`) only when file-path inputs are trusted.

ACP_TELEGRAM_CHANNEL_STATE_FILE
  Path to the MCP channel shared state file (`session_id -> chat_id`, pending media, last active session).
  When unset, the bot uses a file named after its own process id in the system temp directory, so the
  state disappears with that process. Set it to a stable path to carry routing state and queued media
  references across restarts. The bot injects the resolved path into the internal MCP server either way.

ACP_TELEGRAM_CHANNEL_STATE_FILE_<NAME>
  Path to the MCP channel state file of one declared agent, `<NAME>` uppercased with `-`
  replaced by `_`. Wins over the unprefixed {term}`ACP_TELEGRAM_CHANNEL_STATE_FILE`, which
  only ever applies to the implicit `default` agent. Without a pin, a named agent gets
  its own per-agent state directory, such as
  `~/.local/state/telegram-acp-bot/agents/qoder/channel-state.json`, so one
  agent's routing state and queued media never land in another agent's file.
  Example: `ACP_TELEGRAM_CHANNEL_STATE_FILE_QODER=/srv/qoder-state.json`.

ACP_TELEGRAM_BOT_TOKEN
  Telegram bot token used by the internal MCP server when sending attachments.
  Usually injected by the bot runtime for the internal MCP server.
```

## CLI flags

CLI flags take the highest priority and override both environment variables and the config file. Run `telegram-acp-bot --help` for the full list.

Key flags that map to config file or environment variable equivalents:

| CLI flag | Env var | Config key |
|---|---|---|
| `--config` | `ACP_CONFIG_FILE` | — |
| `--telegram-token` | `TELEGRAM_BOT_TOKEN` | `telegram.bot_token` |
| `--allowed-user-id` | `TELEGRAM_ALLOWED_USER_IDS` | `telegram.allowed_user_ids` |
| `--allowed-username` | `TELEGRAM_ALLOWED_USERNAMES` | `telegram.allowed_usernames` |
| `--schedule-language` | `ACP_SCHEDULE_LANGUAGES` | `telegram.schedule_languages` |
| `--group-require-mention` | `ACP_TELEGRAM_GROUP_REQUIRE_MENTION` | `telegram.group_require_mention` |
| `--group-media-queue` | `ACP_TELEGRAM_GROUP_MEDIA_QUEUE` | `telegram.group_media_queue` |
| `--status-reactions` | `ACP_TELEGRAM_STATUS_REACTIONS` | `telegram.status_reactions` |
| `--agent-command` | `ACP_AGENT_COMMAND` | `acp.agent_command` |
| `--agent NAME=COMMAND` | — | `agents` |
| `--only NAME` | — | `only` |
| `--restart-command` | `ACP_RESTART_COMMAND` | `acp.restart_command` |
| `--permission-mode` | `ACP_PERMISSION_MODE` | `acp.permission_mode` |
| `--permission-event-output` | `ACP_PERMISSION_EVENT_OUTPUT` | `acp.permission_event_output` |
| `--acp-stdio-limit` | `ACP_STDIO_LIMIT` | `acp.stdio_limit` |
| `--acp-connect-timeout` | `ACP_CONNECT_TIMEOUT` | `acp.connect_timeout` |
| `--log-format` | `ACP_LOG_FORMAT` | `acp.log_format` |
| `--activity-mode` / `-m` | `ACP_ACTIVITY_MODE` | `acp.activity_mode` |
| `--scheduled-tasks-db` | `ACP_SCHEDULED_TASKS_DB` | `acp.scheduled_tasks_db` |
| `--workspace` | — | `acp.workspace` |

## Migration guide

### From `.env` to `config.json`

If you currently use a `.env` file:

```ini
TELEGRAM_BOT_TOKEN=123456:abc
TELEGRAM_ALLOWED_USER_IDS=123456789
ACP_AGENT_COMMAND=npx @zed-industries/codex-acp
ACP_PERMISSION_MODE=approve
```

The equivalent config file is:

```json
{
  "telegram": {
    "bot_token": "123456:abc",
    "allowed_user_ids": [123456789]
  },
  "acp": {
    "agent_command": "npx @zed-industries/codex-acp",
    "permission_mode": "approve"
  }
}
```

Then run:

```bash
telegram-acp-bot --config ./telegram-acp-bot.json
```

### Mixed setup (env override for secrets)

Keep the token in the environment and everything else in the config file:

```json
{
  "telegram": {
    "allowed_user_ids": [123456789]
  },
  "acp": {
    "agent_command": "npx @zed-industries/codex-acp"
  }
}
```

```bash
TELEGRAM_BOT_TOKEN=123456:abc telegram-acp-bot --config ./telegram-acp-bot.json
```

The environment variable wins over the config file for `bot_token`, so you can rotate the token without touching the file.

## MCP behavior

The bot always advertises an internal MCP stdio server named `telegram-channel`
to the ACP agent. No extra configuration is required.

For adding external MCP servers, see {doc}`mcp`.

(mcp-channel-environment-variables)=
## MCP channel environment variables

- {term}`ACP_TELEGRAM_CHANNEL_ALLOW_PATH` controls whether MCP attachment delivery accepts `path` input.
  Default behavior is disabled, so agents must use `data_base64`.
- {term}`ACP_TELEGRAM_CHANNEL_STATE_FILE` points to runtime state used to resolve session routing for MCP calls.
  Leaving it unset gives each bot process a private file in the temp directory; setting it pins one shared path
  that survives restarts.
- `ACP_TELEGRAM_CHANNEL_STATE_FILE_<NAME>` pins that file for one declared agent, and is the only form a
  named agent reads; the unprefixed variable stays reserved for the implicit `default` agent.
- {term}`ACP_TELEGRAM_BOT_TOKEN` is the token consumed by the MCP server when calling Telegram Bot API.
  The bot injects the instance's own token per agent, so each internal server speaks for its own bot.

Security notes:

- Keep {term}`ACP_TELEGRAM_CHANNEL_ALLOW_PATH` disabled unless your agent is trusted to read local files.
- {term}`ACP_TELEGRAM_CHANNEL_STATE_FILE` and {term}`ACP_TELEGRAM_BOT_TOKEN` are typically managed by the bot process and should not be shared across unrelated runtimes.
