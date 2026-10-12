from __future__ import annotations

# ruff: noqa: F403, F405, I001

import base64

from telegram import Message
from telegram_acp_bot.telegram import bridge as bridge_module
from tests.telegram.support import *

GOAL_COMMAND = AgentCommand(name="goal", description="Set or manage a persistent goal", hint="<objective> | status")
RESEARCH_COMMAND = AgentCommand(name="deep-research", description="Research a topic")


def _fake_message(*, text: str | None = None, caption: str | None = None) -> Message:
    return cast(Message, SimpleNamespace(text=text, caption=caption))


def make_commands_bridge(
    commands: tuple[AgentCommand, ...] | None,
    *,
    group_require_mention: bool = False,
    group_media_queue: bool = False,
) -> tuple[TelegramBridge, AgentCommandsService]:
    service = AgentCommandsService(commands)
    config = make_config(
        token="TOKEN",
        allowed_user_ids=[],
        workspace=".",
        group_require_mention=group_require_mention,
        group_media_queue=group_media_queue,
    )
    return TelegramBridge(config=config, agent_service=cast(AgentService, service)), service


async def test_cmds_lists_advertised_commands_with_description_and_hint():
    bridge, _ = make_commands_bridge((GOAL_COMMAND, RESEARCH_COMMAND))
    update = make_update(with_message=True)

    await bridge.cmds(update, make_context())

    assert update.message is not None
    listing = update.message.replies[0]
    assert "/goal: Set or manage a persistent goal —" in listing
    assert "<objective> | status" in listing
    assert "/deep-research: Research a topic" in listing


async def test_cmds_trims_a_description_longer_than_the_budget():
    bridge, _ = make_commands_bridge((AgentCommand(name="simplify", description="a" * 101),))
    update = make_update(with_message=True)

    await bridge.cmds(update, make_context())

    assert update.message is not None
    assert "/simplify: " + "a" * 99 + "…" in update.message.replies[0]


async def test_cmds_says_no_commands_are_advertised_before_the_first_prompt():
    bridge, _ = make_commands_bridge(())
    update = make_update(with_message=True)

    await bridge.cmds(update, make_context())

    assert update.message is not None
    assert "has not advertised any slash commands yet" in update.message.replies[0]


async def test_cmds_says_the_agent_does_not_advertise_commands_without_the_getter():
    bridge = make_bridge()
    update = make_update(with_message=True)

    await bridge.cmds(update, make_context())

    assert update.message is not None
    assert "has not advertised any slash commands yet" in update.message.replies[0]


async def test_advertised_agent_command_is_forwarded_to_the_prompt_with_its_leading_slash():
    bridge, service = make_commands_bridge((GOAL_COMMAND,))
    update = make_update(text="/goal status")

    await bridge.on_agent_command(update, make_context())

    assert [text for text, _ in service.prompts] == ["/goal status"]


async def test_hyphenated_advertised_agent_command_is_forwarded():
    bridge, service = make_commands_bridge((AgentCommand(name="mcp-config", description="Manage MCP servers"),))
    update = make_update(text="/mcp-config list")

    await bridge.on_agent_command(update, make_context())

    assert [text for text, _ in service.prompts] == ["/mcp-config list"]


async def test_command_mentioning_this_bot_is_forwarded_without_the_mention():
    bridge, service = make_commands_bridge((GOAL_COMMAND,))
    update = make_update(text="/goal@echo_bot status")

    await bridge.on_agent_command(update, make_context())

    assert [text for text, _ in service.prompts] == ["/goal status"]


async def test_command_mentioning_another_bot_keeps_its_own_address():
    bridge, service = make_commands_bridge((GOAL_COMMAND,))
    update = make_update(text="/goal@other_bot status")

    await bridge.on_agent_command(update, make_context())

    assert [text for text, _ in service.prompts] == ["/goal@other_bot status"]


async def test_unadvertised_slash_command_is_rejected_with_a_hint_to_cmds():
    bridge, service = make_commands_bridge((GOAL_COMMAND,))
    update = make_update(text="/qwe poke")

    await bridge.on_agent_command(update, make_context())

    assert update.message is not None
    assert "/qwe is not a command this agent advertises. See /cmds." in update.message.replies[0]
    assert service.prompts == []


async def test_agent_command_in_a_photo_caption_is_forwarded_with_the_media():
    bridge, service = make_commands_bridge((GOAL_COMMAND,))
    context = make_context()
    context.bot.files["photo-1"] = b"bytes"
    update = make_update(caption="/goal review", photo=[SimpleNamespace(file_id="photo-1")])

    await bridge.on_agent_command(update, context)

    assert [text for text, _ in service.prompts] == ["/goal review"]
    expected = base64.b64encode(b"bytes").decode("ascii")
    assert [[image.data_base64 for image in images] for _, images in service.prompts] == [[expected]]


async def test_own_mention_is_stripped_from_a_bare_command_and_from_a_caption():
    bridge, service = make_commands_bridge((GOAL_COMMAND,))
    context = make_context()
    context.bot.files["photo-1"] = b"bytes"

    await bridge.on_agent_command(make_update(text="/goal@echo_bot"), context)
    await bridge.on_agent_command(
        make_update(caption="/goal@echo_bot status", photo=[SimpleNamespace(file_id="photo-1")]),
        context,
    )

    assert [text for text, _ in service.prompts] == ["/goal", "/goal status"]


async def test_agent_command_gate_ignores_text_without_a_leading_command_token():
    bridge, service = make_commands_bridge((GOAL_COMMAND,))

    await bridge.on_agent_command(make_update(text="goal status"), make_context())
    await bridge.on_agent_command(make_update(with_message=False), make_context())

    assert service.prompts == []


async def test_published_command_names_are_matched_case_insensitively_and_without_a_slash():
    bridge, service = make_commands_bridge((AgentCommand(name="/Goal", description="Set a goal"),))

    await bridge.on_agent_command(make_update(text="/goal status"), make_context())

    assert [text for text, _ in service.prompts] == ["/goal status"]


def _installed_callbacks(bridge: TelegramBridge) -> list[object]:
    config = make_config(token="TOKEN", allowed_user_ids=[], workspace=".")
    app = build_application(config, bridge)
    assert list(app.handlers) == [0]
    return [handler.callback for handler in app.handlers[0]]


async def test_bot_commands_are_registered_before_the_agent_command_gate():
    bridge = make_bridge()
    callbacks = _installed_callbacks(bridge)

    assert bridge.cmds in callbacks
    # PTB runs only the first matching handler in a group, so ordering is the precedence rule.
    assert callbacks.index(bridge.help) < callbacks.index(bridge.on_agent_command)
    assert callbacks.index(bridge.on_agent_command) < callbacks.index(bridge.on_message)


async def test_agent_command_filter_matches_a_leading_token_in_text_or_caption():
    gate = bridge_module._AGENT_COMMAND

    assert gate.filter(_fake_message(text="/mcp-config list"))
    assert gate.filter(_fake_message(caption="/goal review"))
    assert not gate.filter(_fake_message(text="hello /goal"))
    assert not gate.filter(_fake_message())


async def test_unmentioned_group_command_is_ignored_without_a_reply():
    bridge, service = make_commands_bridge((GOAL_COMMAND,), group_require_mention=True)
    update = make_update(text="/qwe poke", chat_type="group")

    await bridge.on_agent_command(update, make_context())

    assert update.message is not None
    assert update.message.replies == []
    assert service.prompts == []


async def test_unmentioned_advertised_group_command_is_not_forwarded():
    bridge, service = make_commands_bridge((GOAL_COMMAND,), group_require_mention=True)

    await bridge.on_agent_command(make_update(text="/goal status", chat_type="group"), make_context())

    assert service.prompts == []


async def test_group_command_mentioning_the_bot_is_forwarded_without_the_mention():
    bridge, service = make_commands_bridge((GOAL_COMMAND,), group_require_mention=True)

    await bridge.on_agent_command(make_update(text="/goal@echo_bot status", chat_type="group"), make_context())

    assert [text for text, _ in service.prompts] == ["/goal status"]


async def test_unmentioned_group_command_caption_queues_its_photo_once():
    bridge, service = make_commands_bridge((GOAL_COMMAND,), group_require_mention=True, group_media_queue=True)

    await bridge.on_agent_command(
        make_update(caption="/qwe poke", chat_type="group", photo=[SimpleNamespace(file_id="photo-1")]),
        make_context(),
    )

    assert service.pending_media == [(100, 1, "photo-1", "image/jpeg")]
    assert service.prompts == []


async def test_mentioned_group_command_photo_is_forwarded_and_not_queued():
    bridge, service = make_commands_bridge((GOAL_COMMAND,), group_require_mention=True, group_media_queue=True)
    context = make_context()
    context.bot.files["photo-1"] = b"bytes"

    await bridge.on_agent_command(
        make_update(caption="/goal@echo_bot review", chat_type="group", photo=[SimpleNamespace(file_id="photo-1")]),
        context,
    )

    assert [text for text, _ in service.prompts] == ["/goal review"]
    assert service.pending_media == []


async def test_bot_command_written_in_a_caption_runs_the_bot_handler():
    bridge, service = make_commands_bridge((GOAL_COMMAND,))
    config = make_config(token="TOKEN", allowed_user_ids=[], workspace=".")
    bridge.install(build_application(config, bridge))
    update = make_update(caption="/cmds", photo=[SimpleNamespace(file_id="photo-1")])

    await bridge.on_agent_command(update, make_context())

    assert update.message is not None
    assert "/goal: Set or manage a persistent goal" in update.message.replies[0]
    assert service.prompts == []
