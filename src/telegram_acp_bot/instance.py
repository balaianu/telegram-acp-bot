"""The resolved description of one agent running in this process."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telegram_acp_bot.acp.models import ActivityMode, PermissionEventOutput, PermissionMode
from telegram_acp_bot.telegram.config import StatusReactionLevel


@dataclass(slots=True, frozen=True)
class BotInstanceSpec:
    """Everything needed to build one bot, decided before any resource is opened.

    Keeping this frozen and side-effect free makes the multi-agent surface testable
    without starting Telegram or spawning an agent.
    """

    name: str
    token: str
    allowed_user_ids: tuple[int, ...]
    allowed_usernames: tuple[str, ...]
    workspace: str
    activity_mode: ActivityMode
    schedule_languages: tuple[str, ...]
    group_require_mention: bool
    group_media_queue: bool
    status_reactions: StatusReactionLevel
    command_parts: tuple[str, ...]
    permission_mode: PermissionMode
    permission_event_output: PermissionEventOutput
    stdio_limit: int
    connect_timeout: float
    extra_mcp_servers: dict[str, Any]
    channel_state_file: Path
    scheduled_tasks_db: Path
    restart_command_parts: tuple[str, ...] | None
