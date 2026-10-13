"""
Telegram ACP bot

A Telegram bot that implements Agent Client Protocol to interact with AI agents.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import shlex
import sys
from importlib import metadata
from pathlib import Path
from typing import Any, cast

from dotenv import load_dotenv

from telegram_acp_bot.acp.models import ActivityMode, PermissionEventOutput, PermissionMode
from telegram_acp_bot.agent_config import agent_tokens, resolve_specs
from telegram_acp_bot.agent_identity import DEFAULT_AGENT_NAME
from telegram_acp_bot.config_file import ConfigFileError, load_config_file
from telegram_acp_bot.instance import BotInstanceSpec, build_bot_instance
from telegram_acp_bot.logging_context import configure_logging
from telegram_acp_bot.mcp.state import resolve_state_file
from telegram_acp_bot.register_commands import add_register_commands_subparser
from telegram_acp_bot.runner import run_bot
from telegram_acp_bot.scheduled_tasks import ACP_SCHEDULED_TASKS_DB_ENV, default_scheduled_tasks_db_path
from telegram_acp_bot.telegram.bot import StatusReactionLevel

ALLOWED_USER_IDS_ENV = "TELEGRAM_ALLOWED_USER_IDS"
ALLOWED_USERNAMES_ENV = "TELEGRAM_ALLOWED_USERNAMES"
SCHEDULE_LANGUAGES_ENV = "ACP_SCHEDULE_LANGUAGES"
GROUP_REQUIRE_MENTION_ENV = "ACP_TELEGRAM_GROUP_REQUIRE_MENTION"
GROUP_MEDIA_QUEUE_ENV = "ACP_TELEGRAM_GROUP_MEDIA_QUEUE"
STATUS_REACTIONS_ENV = "ACP_TELEGRAM_STATUS_REACTIONS"
_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})


def get_version() -> str:
    try:
        return metadata.version("telegram-acp-bot")
    except metadata.PackageNotFoundError:
        return "unknown"


def get_parser() -> argparse.ArgumentParser:
    """Return the CLI argument parser."""
    parser = argparse.ArgumentParser(prog="telegram-acp-bot")
    parser.add_argument("-V", "--version", action="version", version=f"%(prog)s {get_version()}")

    subparsers = parser.add_subparsers(dest="subcommand", metavar="command")
    add_register_commands_subparser(subparsers)

    parser.add_argument(
        "--config",
        default=None,
        metavar="PATH",
        help=(
            "Path to a JSON config file. If not provided, the bot will automatically "
            "look for config files in standard locations. Values are overridden by "
            "environment variables and CLI flags. See docs for the full schema."
        ),
    )
    parser.add_argument("--telegram-token", default=os.getenv("TELEGRAM_BOT_TOKEN", ""), help="Telegram bot token")
    parser.add_argument(
        "--agent-command",
        default=os.getenv("ACP_AGENT_COMMAND", ""),
        help="ACP agent command line, e.g. 'codex-acp' or 'uv run examples/echo_agent.py'.",
    )
    parser.add_argument(
        "--allowed-user-id",
        action="append",
        default=[],
        type=int,
        help="Allowed Telegram user ID. Can be repeated.",
    )
    parser.add_argument(
        "--allowed-username",
        action="append",
        default=[],
        help="Allowed Telegram username (with or without @). Can be repeated.",
    )
    parser.add_argument(
        "--agent",
        action="append",
        default=[],
        metavar="NAME=COMMAND",
        help=(
            "Declare or override the ACP command line of one agent, repeatable: "
            "--agent qoder='qodercli --acp' --agent claude=claude-code-acp. Each name is a "
            "separate bot, so give it its own TELEGRAM_BOT_TOKEN_<NAME>."
        ),
    )
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="NAME",
        help="Run just the named agent(s) of the declared set. Can be repeated.",
    )
    parser.add_argument(
        "--workspace",
        default=os.getcwd(),
        help="Default workspace path for /new when path is not provided.",
    )
    parser.add_argument(
        "--schedule-language",
        action="append",
        default=[],
        help=(
            "Language code accepted by natural-language /schedule parsing. Can be repeated; defaults to 'en' and 'es'."
        ),
    )
    parser.add_argument(
        "--permission-mode",
        default=os.getenv("ACP_PERMISSION_MODE", "ask"),
        choices=["ask", "approve", "deny"],
        help="Default ACP permission mode.",
    )
    parser.add_argument(
        "--permission-event-output",
        default=os.getenv("ACP_PERMISSION_EVENT_OUTPUT", "stdout"),
        choices=["stdout", "off"],
        help="Where ACP permission/tool event logs are emitted.",
    )
    parser.add_argument(
        "--acp-stdio-limit",
        default=int(os.getenv("ACP_STDIO_LIMIT", "8388608")),
        type=int,
        help="Asyncio StreamReader limit in bytes for ACP stdio transport.",
    )
    parser.add_argument(
        "--acp-connect-timeout",
        default=float(os.getenv("ACP_CONNECT_TIMEOUT", "30")),
        type=float,
        help="Timeout in seconds for ACP initialize/new_session handshake.",
    )
    parser.add_argument(
        "--restart-command",
        default=os.getenv("ACP_RESTART_COMMAND", ""),
        help="Optional command used by /restart to relaunch the bot (e.g. 'uv run telegram-acp-bot').",
    )
    parser.add_argument(
        "--log-format",
        default=os.getenv("ACP_LOG_FORMAT", "text"),
        choices=["text", "json"],
        help="Application log format.",
    )
    parser.add_argument(
        "-m",
        "--activity-mode",
        default=os.getenv("ACP_ACTIVITY_MODE", "normal"),
        choices=["compact", "normal", "verbose"],
        help=(
            "Activity display mode. "
            "'normal' (default) emits each activity event as its own message. "
            "'compact' collapses all intermediate events into a single in-place status message "
            "that is replaced by the final answer when the agent responds. "
            "'verbose' streams append-only updates for active reply and tool activity."
        ),
    )
    parser.add_argument(
        "--group-require-mention",
        action="store_true",
        default=_env_flag(GROUP_REQUIRE_MENTION_ENV),
        help=(
            "Ignore group messages that do not @mention the bot (or reply to one of its messages). "
            "Private chats are always answered. Also enabled by a truthy "
            f"{GROUP_REQUIRE_MENTION_ENV}."
        ),
    )
    parser.add_argument(
        "--group-media-queue",
        action="store_true",
        default=_env_flag(GROUP_MEDIA_QUEUE_ENV),
        help=(
            "Keep group media that the mention gate ignores as file_id references, then announce them "
            "on the next accepted prompt so the agent can download only what it needs. "
            f"Also enabled by a truthy {GROUP_MEDIA_QUEUE_ENV}."
        ),
    )
    parser.add_argument(
        "--status-reactions",
        default=os.getenv(STATUS_REACTIONS_ENV, "off"),
        choices=["off", "minimal"],
        help=(
            "React on the prompt message with the bot's own lifecycle emoji. "
            "'off' (default) adds no bot reactions; the agent can still react through its MCP tool. "
            "'minimal' marks 👀 while the chat is busy, 🤔 while the agent works, then 👍 or 💔 "
            "when the cycle ends. "
            f"Also set by {STATUS_REACTIONS_ENV} or `telegram.status_reactions`."
        ),
    )
    parser.add_argument(
        "--scheduled-tasks-db",
        default=os.getenv(ACP_SCHEDULED_TASKS_DB_ENV, str(default_scheduled_tasks_db_path())),
        help="SQLite database path used for deferred scheduled follow-ups.",
    )
    return parser


def _apply_config_file_defaults(
    parser: argparse.ArgumentParser,
    config: dict[str, Any],
) -> None:
    """Apply config file values as parser-level defaults.

    Only takes effect when the corresponding environment variable is not set.
    CLI flags always take precedence over these defaults.
    """
    tg = config.get("telegram", {})
    acp_cfg = config.get("acp", {})
    updates: dict[str, object] = {}

    # String settings: env var > config file > hardcoded default in add_argument
    _str_map: list[tuple[str, str, Any]] = [
        ("telegram_token", "TELEGRAM_BOT_TOKEN", tg.get("bot_token")),
        ("agent_command", "ACP_AGENT_COMMAND", acp_cfg.get("agent_command")),
        ("restart_command", "ACP_RESTART_COMMAND", acp_cfg.get("restart_command")),
        ("permission_mode", "ACP_PERMISSION_MODE", acp_cfg.get("permission_mode")),
        ("permission_event_output", "ACP_PERMISSION_EVENT_OUTPUT", acp_cfg.get("permission_event_output")),
        ("log_format", "ACP_LOG_FORMAT", acp_cfg.get("log_format")),
        ("activity_mode", "ACP_ACTIVITY_MODE", acp_cfg.get("activity_mode")),
        ("status_reactions", STATUS_REACTIONS_ENV, tg.get("status_reactions")),
        ("scheduled_tasks_db", ACP_SCHEDULED_TASKS_DB_ENV, acp_cfg.get("scheduled_tasks_db")),
    ]
    updates |= {
        dest: cfg_val for dest, env_name, cfg_val in _str_map if not os.getenv(env_name) and cfg_val is not None
    }

    # workspace has no env var
    if "workspace" in acp_cfg:
        updates["workspace"] = acp_cfg["workspace"]

    # Numeric settings
    if not os.getenv("ACP_STDIO_LIMIT") and "stdio_limit" in acp_cfg:
        updates["acp_stdio_limit"] = int(acp_cfg["stdio_limit"])
    if not os.getenv("ACP_CONNECT_TIMEOUT") and "connect_timeout" in acp_cfg:
        updates["acp_connect_timeout"] = float(acp_cfg["connect_timeout"])

    if updates:
        _set_parser_defaults(parser, updates)


def _set_parser_defaults(parser: argparse.ArgumentParser, updates: dict[str, object]) -> None:
    """Apply defaults to *parser* and any subparsers that expose the same dests."""
    parser_dests = {action.dest for action in parser._actions}
    parser_updates = {dest: value for dest, value in updates.items() if dest in parser_dests}
    if parser_updates:
        parser.set_defaults(**parser_updates)

    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for subparser in action.choices.values():
                _set_parser_defaults(subparser, updates)


def _parse_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env_flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in _TRUE_VALUES


def _normalize_username(username: str) -> str:
    return username.lstrip("@").strip().lower()


def _resolve_allowed_users(
    *,
    parser: argparse.ArgumentParser,
    opts: argparse.Namespace,
    config_data: dict[str, Any] | None = None,
) -> tuple[list[int], list[str]]:
    try:
        env_allowed_ids = [int(item) for item in _parse_csv(os.getenv(ALLOWED_USER_IDS_ENV, ""))]
    except ValueError:
        parser.error(f"{ALLOWED_USER_IDS_ENV} must be a comma-separated list of integers")
    env_allowed_usernames = [_normalize_username(item) for item in _parse_csv(os.getenv(ALLOWED_USERNAMES_ENV, ""))]
    cli_allowed_usernames = [_normalize_username(item) for item in opts.allowed_username]
    allowed_user_ids = [*env_allowed_ids, *opts.allowed_user_id]
    allowed_usernames = [*env_allowed_usernames, *cli_allowed_usernames]

    # Config file: used as a fallback when neither env vars nor CLI provided any allowed users
    if not allowed_user_ids and not allowed_usernames and config_data:
        tg = config_data.get("telegram", {})
        allowed_user_ids = list(tg.get("allowed_user_ids", []))
        allowed_usernames = [_normalize_username(u) for u in tg.get("allowed_usernames", [])]

    if not allowed_user_ids and not allowed_usernames:
        parser.error(
            "--allowed-user-id/--allowed-username (or TELEGRAM_ALLOWED_USER_IDS/TELEGRAM_ALLOWED_USERNAMES) "
            "must include at least one allowed user",
        )
    return allowed_user_ids, allowed_usernames


def _resolve_schedule_languages(
    *,
    opts: argparse.Namespace,
    config_data: dict[str, Any] | None = None,
) -> list[str]:
    cli_languages = [language.strip() for language in opts.schedule_language if language.strip()]
    if cli_languages:
        return [language.lower() for language in cli_languages]

    env_languages = _parse_csv(os.getenv(SCHEDULE_LANGUAGES_ENV, ""))
    if env_languages:
        return [language.lower() for language in env_languages]

    if config_data:
        telegram_cfg = config_data.get("telegram", {})
        configured = telegram_cfg.get("schedule_languages", [])
        if configured:
            return [str(language).strip().lower() for language in configured if str(language).strip()]

    return ["en", "es"]


def _base_spec(
    *,
    parser: argparse.ArgumentParser,
    opts: argparse.Namespace,
    config_data: dict[str, Any],
) -> BotInstanceSpec:
    """Resolve the flat config into the spec every named agent inherits from.

    With no `agents` declared, this is also the only spec, which is why the
    single-bot deployment keeps exactly the values it had before multi-agent support.
    """
    if not opts.telegram_token:
        parser.error("--telegram-token (or TELEGRAM_BOT_TOKEN) is required")
    if not opts.agent_command:
        parser.error("--agent-command (or ACP_AGENT_COMMAND) is required")
    if opts.acp_stdio_limit <= 0:
        parser.error("--acp-stdio-limit must be a positive integer")
    if opts.acp_connect_timeout <= 0:
        parser.error("--acp-connect-timeout must be a positive number")
    allowed_user_ids, allowed_usernames = _resolve_allowed_users(parser=parser, opts=opts, config_data=config_data)
    schedule_languages = _resolve_schedule_languages(opts=opts, config_data=config_data)
    command_parts = shlex.split(opts.agent_command)
    if not command_parts:
        parser.error("--agent-command is empty after parsing")

    return BotInstanceSpec(
        name=DEFAULT_AGENT_NAME,
        token=opts.telegram_token,
        allowed_user_ids=tuple(allowed_user_ids),
        allowed_usernames=tuple(allowed_usernames),
        workspace=opts.workspace,
        activity_mode=cast("ActivityMode", opts.activity_mode),
        schedule_languages=tuple(schedule_languages),
        group_require_mention=opts.group_require_mention
        or bool(config_data.get("telegram", {}).get("group_require_mention", False)),
        group_media_queue=opts.group_media_queue
        or bool(config_data.get("telegram", {}).get("group_media_queue", False)),
        status_reactions=cast("StatusReactionLevel", opts.status_reactions),
        command_parts=tuple(command_parts),
        permission_mode=cast("PermissionMode", opts.permission_mode),
        permission_event_output=cast("PermissionEventOutput", opts.permission_event_output),
        stdio_limit=int(opts.acp_stdio_limit),
        connect_timeout=float(opts.acp_connect_timeout),
        extra_mcp_servers=config_data.get("mcp_servers") or {},
        channel_state_file=resolve_state_file(pid=os.getpid()),
        scheduled_tasks_db=Path(opts.scheduled_tasks_db).expanduser(),
        restart_command_parts=tuple(shlex.split(opts.restart_command)) if opts.restart_command.strip() else None,
    )


def _find_config_file() -> Path | None:
    """Find a config file from standard locations.

    Returns the first readable config file from:
    1. ./.telegram_acp_bot/config.json
    2. $XDG_CONFIG_HOME/telegram_acp_bot/config.json
       (or ~/.config/telegram_acp_bot/config.json when XDG_CONFIG_HOME is unset)
    3. ~/.telegram_acp_bot/config.json

    Returns None if no config file is found.
    """
    xdg_config_home = Path(os.environ.get("XDG_CONFIG_HOME") or "~/.config").expanduser()
    candidates = [
        Path(".telegram_acp_bot/config.json"),
        xdg_config_home / "telegram_acp_bot" / "config.json",
        Path("~/.telegram_acp_bot/config.json").expanduser(),
    ]

    for path in candidates:
        if path.is_file():
            return path
    return None


def _load_preparsed_config(
    parser: argparse.ArgumentParser,
    config_arg: str | None,
) -> dict[str, Any]:
    """Load config-file defaults selected during pre-parsing."""
    if config_arg is not None:
        if not config_arg.strip():
            parser.error("--config must not be empty or whitespace")
        config_path = Path(config_arg).expanduser()
    else:
        config_path = _find_config_file()

    if config_path is None:
        return {}

    try:
        config_data = load_config_file(config_path)
    except ConfigFileError as exc:
        parser.error(str(exc))
    _apply_config_file_defaults(parser, config_data)
    return config_data


def main(args: list[str] | None = None) -> int:
    """Run the main program.

    Dispatches to the appropriate sub-command when one is given (e.g.
    `register-commands`), otherwise runs every declared agent as its own Telegram
    bot inside this one process. Sub-commands are registered via argparse
    subparsers and advertised in `--help`.
    """
    load_dotenv(override=False)
    parser = get_parser()
    argv = list(args) if args is not None else sys.argv[1:]

    # Pre-parse to extract --config before full argument resolution
    pre_opts, _ = parser.parse_known_args(args=argv)

    config_data = _load_preparsed_config(parser, pre_opts.config)

    # Full parse: catches unknown/invalid flags for both the main command and subcommands.
    opts = parser.parse_args(args=argv)

    # Sub-command dispatch: each sub-command sets opts.func via set_defaults.
    if hasattr(opts, "func"):
        agents = agent_tokens(config_data=config_data, env=os.environ, fallback_token=opts.telegram_token)
        return opts.func(opts, tokens=agents)

    log_level = os.getenv("ACP_LOG_LEVEL") or config_data.get("acp", {}).get("log_level", "INFO")
    configure_logging(
        level=getattr(logging, str(log_level).upper(), logging.INFO),
        log_format=opts.log_format,
        close_replaced_handlers=True,
    )

    base = _base_spec(parser=parser, opts=opts, config_data=config_data)
    try:
        specs = resolve_specs(
            base=base,
            config_data=config_data,
            agent_flags=opts.agent,
            only=opts.only or list(config_data.get("only") or []),
            env=os.environ,
            pid=os.getpid(),
        )
    except ConfigFileError as exc:
        parser.error(str(exc))

    stop_event = asyncio.Event()
    return run_bot([build_bot_instance(spec, stop_event=stop_event) for spec in specs], stop_event)


__all__: list[str] = ["get_parser", "main"]
