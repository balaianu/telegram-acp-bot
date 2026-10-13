"""Tests for wiring a resolved spec into one bot."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from acp.schema import HttpMcpServer, McpServerStdio

from telegram_acp_bot.instance import build_bot_instance, build_mcp_servers
from telegram_acp_bot.mcp.state import STATE_FILE_ENV, TOKEN_ENV
from telegram_acp_bot.scheduled_tasks import ACP_SCHEDULED_TASKS_DB_ENV, ScheduledTaskScheduler, ScheduledTaskStore
from telegram_acp_bot.telegram.bridge import TelegramBridge
from tests.test_agent_config import base_spec

EXTRA_STATE = Path("/agents/qoder/channel-state.json")
EXTRA_DB = Path("/agents/qoder/scheduled-tasks.sqlite3")
OTHER_TOKEN = "222:other"


def test_build_bot_instance_hands_the_bridge_the_shared_stop_event():
    stop_event = asyncio.Event()
    instance = build_bot_instance(base_spec(), stop_event=stop_event)

    assert isinstance(instance.bridge, TelegramBridge)
    assert stop_event.is_set() is False
    stop_request = instance.bridge._stop_request
    assert stop_request is not None
    stop_request()
    assert stop_event.is_set()


def test_build_bot_instance_keeps_the_paths_of_its_own_agent(tmp_path: Path):
    spec = base_spec(channel_state_file=tmp_path / "state.json", scheduled_tasks_db=tmp_path / "tasks.sqlite3")
    instance = build_bot_instance(spec, stop_event=asyncio.Event())

    assert instance.service._channel_state_file == spec.channel_state_file
    assert isinstance(instance.store, ScheduledTaskStore)
    assert instance.store.path == spec.scheduled_tasks_db
    assert isinstance(instance.scheduler, ScheduledTaskScheduler)


def test_build_bot_instance_translates_the_spec_into_a_bot_config():
    instance = build_bot_instance(base_spec(), stop_event=asyncio.Event())

    assert instance.config.token == base_spec().token
    assert instance.config.allowed_user_ids == {1}
    assert instance.config.allowed_usernames == {"someone"}
    assert instance.config.schedule_languages == ("en", "es")
    assert instance.config.activity_mode == "normal"


def test_build_bot_instance_starts_the_declared_agent_command():
    spec = base_spec(command_parts=("qodercli", "--acp", "--yolo"))
    instance = build_bot_instance(spec, stop_event=asyncio.Event())

    assert instance.service._program == "qodercli"
    assert instance.service._args == ["--acp", "--yolo"]


def test_build_mcp_servers_points_the_internal_server_at_this_instance():
    servers = build_mcp_servers(
        telegram_token=OTHER_TOKEN,
        state_file=EXTRA_STATE,
        scheduled_tasks_db=EXTRA_DB,
    )

    assert len(servers) == 1
    server = servers[0]
    assert isinstance(server, McpServerStdio)
    assert server.name == "telegram-channel"
    assert server.command == sys.executable
    assert {item.name: item.value for item in server.env} == {
        TOKEN_ENV: OTHER_TOKEN,
        STATE_FILE_ENV: str(EXTRA_STATE),
        ACP_SCHEDULED_TASKS_DB_ENV: str(EXTRA_DB),
    }


def test_build_mcp_servers_appends_configured_servers():
    servers = build_mcp_servers(
        telegram_token=OTHER_TOKEN,
        state_file=EXTRA_STATE,
        scheduled_tasks_db=EXTRA_DB,
        extra_servers={
            "local": {"command": "uv", "args": ["run", "echo.py"], "env": {"KEY": "val"}},
            "remote": {"url": "https://mcp.example.com/mcp", "headers": {"Auth": "Bearer tok"}},
        },
    )

    assert [server.name for server in servers] == ["telegram-channel", "local", "remote"]
    local = servers[1]
    assert isinstance(local, McpServerStdio)
    assert {item.name: item.value for item in local.env} == {"KEY": "val"}
    remote = servers[2]
    assert isinstance(remote, HttpMcpServer)
    assert {item.name: item.value for item in remote.headers} == {"Auth": "Bearer tok"}


def test_build_mcp_servers_lets_an_external_channel_override_the_internal_one():
    servers = build_mcp_servers(
        telegram_token=OTHER_TOKEN,
        state_file=EXTRA_STATE,
        scheduled_tasks_db=EXTRA_DB,
        extra_servers={"telegram-channel": {"command": "custom-server"}},
    )

    assert len(servers) == 1
    assert servers[0].command == "custom-server"
