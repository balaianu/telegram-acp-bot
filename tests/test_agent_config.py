"""Tests for the agent precedence resolver."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from telegram_acp_bot.agent_config import TOKEN_ENV, resolve_specs
from telegram_acp_bot.config_file import ConfigFileError
from telegram_acp_bot.instance import BotInstanceSpec

QODER_TOKEN = "111:qoder"
CLAUDE_TOKEN = "222:claude"
BASE_DB = Path("/base/scheduled-tasks.sqlite3")
BASE_STATE = Path("/base/channel-state.json")
AGENT_STDIO_LIMIT = 1024
AGENT_CONNECT_TIMEOUT = 5.0
TEST_PID = 4242


def base_spec(**overrides: object) -> BotInstanceSpec:
    """A single-agent spec standing in for what `main()` resolves from the flat config."""
    spec = BotInstanceSpec(
        name="default",
        token=QODER_TOKEN,
        allowed_user_ids=(1,),
        allowed_usernames=("someone",),
        workspace="/workspace",
        activity_mode="normal",
        schedule_languages=("en", "es"),
        group_require_mention=False,
        group_media_queue=False,
        status_reactions="off",
        command_parts=("fake-agent",),
        permission_mode="ask",
        permission_event_output="stdout",
        stdio_limit=8388608,
        connect_timeout=30.0,
        extra_mcp_servers={"shared": {"command": "base-server"}},
        channel_state_file=BASE_STATE,
        scheduled_tasks_db=BASE_DB,
        restart_command_parts=None,
    )
    return replace(spec, **overrides)


def resolve(
    *,
    base: BotInstanceSpec | None = None,
    agents: dict[str, object] | None = None,
    agent_flags: list[str] | None = None,
    only: list[str] | None = None,
    env: dict[str, str] | None = None,
) -> list[BotInstanceSpec]:
    config_data = {} if agents is None else {"agents": agents}
    return resolve_specs(
        base=base or base_spec(),
        config_data=config_data,
        agent_flags=agent_flags or [],
        only=only or [],
        env=env or {},
        pid=TEST_PID,
    )


def test_a_flat_config_still_describes_one_agent():
    base = base_spec()

    assert resolve(base=base) == [base]


def test_declaration_order_is_the_run_order():
    specs = resolve(agents={"claude": {"telegram": {"bot_token": CLAUDE_TOKEN}}, "qoder": {}})

    assert [spec.name for spec in specs] == ["claude", "qoder"]


def test_an_agent_inherits_every_field_it_does_not_override():
    (spec,) = resolve(agents={"qoder": {}})
    base = base_spec()

    assert spec.name == "qoder"
    assert spec.allowed_user_ids == base.allowed_user_ids
    assert spec.allowed_usernames == base.allowed_usernames
    assert spec.workspace == base.workspace
    assert spec.command_parts == base.command_parts
    assert spec.permission_mode == base.permission_mode
    assert spec.stdio_limit == base.stdio_limit
    assert spec.connect_timeout == base.connect_timeout
    assert spec.status_reactions == base.status_reactions


def test_an_agent_overrides_the_fields_it_names():
    (spec,) = resolve(
        agents={
            "qoder": {
                "telegram": {
                    "bot_token": QODER_TOKEN,
                    "allowed_user_ids": [7, 8],
                    "allowed_usernames": ["@Alpha"],
                    "schedule_languages": ["VI"],
                    "group_require_mention": True,
                    "group_media_queue": True,
                    "status_reactions": "minimal",
                },
                "acp": {
                    "agent_command": "qodercli --acp --yolo",
                    "workspace": "~/other",
                    "activity_mode": "compact",
                    "permission_mode": "approve",
                    "permission_event_output": "off",
                    "stdio_limit": AGENT_STDIO_LIMIT,
                    "connect_timeout": AGENT_CONNECT_TIMEOUT,
                    "restart_command": "botctl restart",
                },
            }
        }
    )

    assert spec.allowed_user_ids == (7, 8)
    assert spec.allowed_usernames == ("alpha",)
    assert spec.schedule_languages == ("vi",)
    assert spec.group_require_mention is True
    assert spec.group_media_queue is True
    assert spec.status_reactions == "minimal"
    assert spec.command_parts == ("qodercli", "--acp", "--yolo")
    assert spec.workspace == "~/other"
    assert spec.activity_mode == "compact"
    assert spec.permission_mode == "approve"
    assert spec.permission_event_output == "off"
    assert spec.stdio_limit == AGENT_STDIO_LIMIT
    assert spec.connect_timeout == AGENT_CONNECT_TIMEOUT
    assert spec.restart_command_parts == ("botctl", "restart")


def test_a_named_agent_falls_back_to_the_base_restart_command():
    base = base_spec(restart_command_parts=("botctl", "restart"))

    (spec,) = resolve(base=base, agents={"qoder": {}})

    assert spec.restart_command_parts == ("botctl", "restart")


def test_an_empty_restart_command_does_not_hide_the_base_one():
    base = base_spec(restart_command_parts=("botctl", "restart"))

    (spec,) = resolve(base=base, agents={"qoder": {"acp": {"restart_command": "   "}}})

    assert spec.restart_command_parts == ("botctl", "restart")


def test_agent_token_prefers_the_per_agent_environment_variable():
    specs = resolve(
        agents={"qoder": {"telegram": {"bot_token": CLAUDE_TOKEN}}},
        env={f"{TOKEN_ENV}_QODER": f" {QODER_TOKEN} "},
    )

    assert [spec.token for spec in specs] == [QODER_TOKEN]


def test_agent_token_falls_back_to_the_base_token():
    base = base_spec(token=QODER_TOKEN)

    (spec,) = resolve(base=base, agents={"qoder": {}})

    assert spec.token == QODER_TOKEN


def test_two_agents_cannot_share_one_token():
    with pytest.raises(ConfigFileError, match="agents 'qoder' and 'claude' share one bot token"):
        resolve(agents={"qoder": {}, "claude": {}})


def test_two_agents_with_distinct_tokens_are_accepted():
    specs = resolve(agents={"qoder": {}, "claude": {"telegram": {"bot_token": CLAUDE_TOKEN}}})

    assert [spec.token for spec in specs] == [QODER_TOKEN, CLAUDE_TOKEN]


def test_a_hyphenated_agent_name_reads_an_underscored_variable():
    (spec,) = resolve(agents={"claude-code": {}}, env={f"{TOKEN_ENV}_CLAUDE_CODE": CLAUDE_TOKEN})

    assert spec.token == CLAUDE_TOKEN


def test_the_agent_flag_wins_over_the_configured_command():
    specs = resolve(
        agents={"qoder": {"acp": {"agent_command": "from-config"}}},
        agent_flags=["qoder=from-flag"],
    )

    assert specs[0].command_parts == ("from-flag",)


def test_the_agent_flag_declares_an_agent_the_config_never_mentioned():
    specs = resolve(
        agents={"claude": {"telegram": {"bot_token": CLAUDE_TOKEN}}},
        agent_flags=["qoder=fake-agent"],
        env={f"{TOKEN_ENV}_QODER": QODER_TOKEN},
    )

    assert [spec.name for spec in specs] == ["claude", "qoder"]
    assert specs[1].command_parts == ("fake-agent",)


def test_a_flag_without_a_command_is_rejected():
    with pytest.raises(ConfigFileError, match="--agent expects NAME=COMMAND, got 'qoder'"):
        resolve(agent_flags=["qoder"], agents={"claude": {}})


def test_a_flag_with_an_empty_command_is_rejected():
    with pytest.raises(ConfigFileError, match=r"--agent qoder= needs a command"):
        resolve(agent_flags=["qoder=  "], agents={"claude": {}})


def test_a_flag_with_an_unsafe_name_is_rejected():
    with pytest.raises(ConfigFileError, match="--agent:"):
        resolve(agent_flags=["Qoder=fake-agent"], agents={"claude": {}})


def test_an_agent_without_any_command_is_rejected():
    base = base_spec(command_parts=())

    with pytest.raises(ConfigFileError, match="agent 'qoder' has no ACP command"):
        resolve(base=base, agents={"qoder": {}})


def test_only_runs_a_declared_subset():
    specs = resolve(
        agents={"qoder": {}, "claude": {"telegram": {"bot_token": CLAUDE_TOKEN}}},
        only=["claude"],
    )

    assert [spec.name for spec in specs] == ["claude"]


def test_only_rejects_an_undeclared_name():
    with pytest.raises(ConfigFileError, match="--only names unknown agent\\(s\\) \\['ghost'\\]"):
        resolve(agents={"qoder": {}}, only=["ghost"])


def test_a_named_agent_gets_its_own_state_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    monkeypatch.delenv("ACP_TELEGRAM_CHANNEL_STATE_FILE", raising=False)

    (spec,) = resolve(agents={"qoder": {}})

    assert spec.channel_state_file == tmp_path / "telegram-acp-bot" / "agents" / "qoder" / "channel-state.json"


def test_a_state_file_pinned_per_agent_wins(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    pinned = tmp_path / "pinned.json"

    (spec,) = resolve(agents={"qoder": {}}, env={"ACP_TELEGRAM_CHANNEL_STATE_FILE_QODER": str(pinned)})

    assert spec.channel_state_file == pinned


def test_the_default_agent_keeps_the_base_state_file_and_database():
    base = base_spec()

    (spec,) = resolve(base=base, agents={"default": {}})

    assert spec.channel_state_file != BASE_STATE
    assert spec.scheduled_tasks_db == BASE_DB


def test_a_database_pinned_per_agent_wins():
    (spec,) = resolve(agents={"qoder": {}}, env={"ACP_SCHEDULED_TASKS_DB_QODER": "/tmp/qoder.sqlite3"})

    assert spec.scheduled_tasks_db == Path("/tmp/qoder.sqlite3")


def test_an_agent_section_can_name_its_database():
    (spec,) = resolve(agents={"qoder": {"acp": {"scheduled_tasks_db": "~/qoder.sqlite3"}}})

    assert spec.scheduled_tasks_db == Path.home() / "qoder.sqlite3"


def test_a_named_agent_defaults_to_its_own_database(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    specs = resolve(agents={"qoder": {}, "claude": {"telegram": {"bot_token": CLAUDE_TOKEN}}})

    assert [spec.scheduled_tasks_db for spec in specs] == [
        tmp_path / "telegram-acp-bot" / "agents" / "qoder" / "scheduled-tasks.sqlite3",
        tmp_path / "telegram-acp-bot" / "agents" / "claude" / "scheduled-tasks.sqlite3",
    ]


def test_agents_merge_mcp_servers_by_name_and_win():
    (spec,) = resolve(
        agents={
            "qoder": {
                "mcp_servers": {
                    "shared": {"command": "agent-server"},
                    "extra": {"url": "https://example.test/mcp"},
                }
            }
        }
    )

    assert spec.extra_mcp_servers == {
        "shared": {"command": "agent-server"},
        "extra": {"url": "https://example.test/mcp"},
    }
