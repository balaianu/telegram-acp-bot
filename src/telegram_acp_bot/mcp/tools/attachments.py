"""Attachment-delivery tool for the internal Telegram MCP server."""

from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import cast

from mcp.server.fastmcp import FastMCP
from telegram import Bot, InputFile

from telegram_acp_bot.agent_identity import DEFAULT_AGENT_NAME
from telegram_acp_bot.mcp.context import resolve_request_context
from telegram_acp_bot.mcp.state import AGENT_NAME_ENV

ALLOW_PATH_ENV = "ACP_TELEGRAM_CHANNEL_ALLOW_PATH"
EXACTLY_ONE_ATTACHMENT_SOURCE_ERROR = "provide exactly one of `path` or `data_base64`"
MIXED_TOP_LEVEL_AND_ATTACHMENTS_ERROR = (
    "use either the top-level `path`/`data_base64` or the `attachments` list, not both"
)
EMPTY_ATTACHMENTS_ERROR = "`attachments` is empty"
DEFAULT_MEDIA_DIRNAME = ".telegram-media"
MEDIA_OUTSIDE_WORKSPACE_ERROR = "`dest` must stay inside the agent working directory"


@dataclass(frozen=True, slots=True)
class AttachmentPayload:
    raw: bytes
    filename: str
    guessed_mime: str | None


def register_attachment_tools(mcp: FastMCP) -> None:
    """Register attachment tools on the provided MCP server."""

    mcp.tool(
        name="telegram_send_attachment",
        description=(
            "Send one or more attachments to the current Telegram chat. "
            "If session_id is omitted, the server auto-resolves it when possible. "
            "Use this when the user asks to send an image/file.\n\n"
            "Choosing the input: when the content is already a file on disk (a screenshot, an "
            "export, a generated PDF), pass its location with `path` — do NOT read the bytes and "
            "hand-encode them into `data_base64`. base64 is only for bytes you produced in memory "
            "and never wrote to disk, and it requires enabling `path` via "
            f"{ALLOW_PATH_ENV}=1. Reserve `data_base64` for in-memory bytes.\n\n"
            "Delivery is chosen from the mime type so media plays natively in Telegram: "
            "`image/*` as a photo, `video/*` as a video, `audio/*` as audio, anything else as a "
            "file. Pass `mime_type` to force a type, or `name` when sending base64 so the type can "
            "be inferred from the extension.\n\n"
            "To send several files in one call, pass `attachments` as a list of objects, each with "
            "`path` or `data_base64` plus optional `name` and `mime_type` (do not combine it with "
            "the top-level `path`/`data_base64`)."
        ),
    )(telegram_send_attachment)

    mcp.tool(
        name="telegram_fetch_attachment",
        description=(
            "Download one Telegram attachment by file_id into the agent working directory and return its path. "
            "Use this for media that was announced as pending instead of being delivered inline: read it only "
            "when the conversation actually needs the pixels or the file contents. Pass the announced mime_type "
            "so the saved file gets a usable extension, or pass dest to choose the path yourself. "
            "The returned path can be handed straight back to telegram_send_attachment as `path` instead of "
            "re-encoding it to base64. "
            "Telegram only serves a file for about an hour after it arrives, so fetch pending media promptly."
        ),
    )(telegram_fetch_attachment)


async def telegram_fetch_attachment(
    file_id: str,
    session_id: str | None = None,
    dest: str | None = None,
    mime_type: str | None = None,
) -> dict[str, object]:
    def fail(error: str) -> dict[str, object]:
        return {"ok": False, "error": error}

    context = resolve_request_context(session_id=session_id)
    if isinstance(context, str):
        return fail(context)

    target = resolve_media_target(dest=dest, file_id=file_id, mime_type=mime_type)
    if isinstance(target, str):
        return fail(target)

    bot = Bot(token=context.token)
    try:
        tg_file = await bot.get_file(file_id)
        raw = bytes(await tg_file.download_as_bytearray())
    except Exception as exc:  # noqa: BLE001
        return fail(f"download failed for file_id `{file_id}`: {exc}")

    if not raw:
        return fail(f"Telegram returned an empty payload for file_id `{file_id}`")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return {
        "ok": True,
        "session_id": context.session_id,
        "chat_id": context.chat_id,
        "path": str(target),
        "bytes": len(raw),
        "mime_type": mimetypes.guess_type(target.name)[0] or mime_type,
    }


def _media_dirname() -> str:
    """The media subdirectory for this agent, namespaced so two agents never share files."""

    agent = os.getenv(AGENT_NAME_ENV, "").strip()
    if agent and agent != DEFAULT_AGENT_NAME:
        return f"{DEFAULT_MEDIA_DIRNAME}/{agent}"
    return DEFAULT_MEDIA_DIRNAME


def resolve_media_target(*, dest: str | None, file_id: str, mime_type: str | None = None) -> Path | str:
    """Resolve where a fetched attachment may be written, keeping it inside the working directory."""

    root = Path.cwd().resolve(strict=False)
    if dest is None:
        return root / _media_dirname() / default_media_name(file_id, mime_type)

    candidate = Path(dest).expanduser()
    resolved = (candidate if candidate.is_absolute() else root / candidate).resolve(strict=False)
    if root not in resolved.parents and resolved != root:
        return MEDIA_OUTSIDE_WORKSPACE_ERROR
    if resolved.is_dir():
        return f"`dest` is a directory: {resolved}"
    return resolved


def default_media_name(file_id: str, mime_type: str | None) -> str:
    """Build the stored name for a fetched attachment from its file id and announced mime type."""

    digest = hashlib.sha256(file_id.encode("utf-8")).hexdigest()[:16]
    if not mime_type:
        return digest
    if mime_type == "image/jpeg":
        return f"{digest}.jpg"
    return f"{digest}{mimetypes.guess_extension(mime_type) or ''}"


async def telegram_send_attachment(  # noqa: PLR0913, PLR0917
    session_id: str | None = None,
    path: str | None = None,
    data_base64: str | None = None,
    name: str | None = None,
    mime_type: str | None = None,
    attachments: list[dict[str, str]] | None = None,
) -> dict[str, object]:
    def fail(error: str) -> dict[str, object]:
        return {"ok": False, "error": error}

    specs = build_send_specs(
        path=path, data_base64=data_base64, name=name, mime_type=mime_type, attachments=attachments
    )
    if isinstance(specs, str):
        return fail(specs)

    if any(spec.get("path") for spec in specs) and not allow_path_inputs():
        return fail(f"`path` input is disabled by default. Set {ALLOW_PATH_ENV}=1 to enable it.")

    context = resolve_request_context(session_id=session_id)
    if isinstance(context, str):
        return fail(context)

    loaded_payloads: list[tuple[AttachmentPayload, str]] = []
    for spec in specs:
        loaded = load_attachment_bytes(
            path=spec.get("path"), data_base64=spec.get("data_base64"), name=spec.get("name")
        )
        if isinstance(loaded, str):
            return fail(loaded)
        resolved_mime = spec.get("mime_type") or loaded.guessed_mime or "application/octet-stream"
        loaded_payloads.append((loaded, resolved_mime))

    bot = Bot(token=context.token)
    delivered: list[dict[str, object]] = []
    for payload, resolved_mime in loaded_payloads:
        delivered_as = await deliver_attachment(
            bot=bot,
            chat_id=context.chat_id,
            payload=payload,
            resolved_mime=resolved_mime,
        )
        delivered.append(
            {
                "name": payload.filename,
                "mime_type": resolved_mime,
                "delivered_as": delivered_as,
                "bytes": len(payload.raw),
            }
        )

    result: dict[str, object] = {
        "ok": True,
        "session_id": context.session_id,
        "chat_id": context.chat_id,
        "count": len(delivered),
        "delivered": delivered,
    }
    if len(delivered) == 1:
        only = delivered[0]
        result.update({"delivered_as": only["delivered_as"], "name": only["name"], "mime_type": only["mime_type"]})
    return result


def build_send_specs(
    *,
    path: str | None,
    data_base64: str | None,
    name: str | None,
    mime_type: str | None,
    attachments: list[dict[str, str]] | None,
) -> list[dict[str, str]] | str:
    """Normalize the single-file and multi-file inputs into one list of per-attachment specs."""

    if attachments is not None:
        if path is not None or data_base64 is not None:
            return MIXED_TOP_LEVEL_AND_ATTACHMENTS_ERROR
        if not attachments:
            return EMPTY_ATTACHMENTS_ERROR
        return attachments

    if path is None and data_base64 is None:
        return EXACTLY_ONE_ATTACHMENT_SOURCE_ERROR
    spec: dict[str, str] = {}
    if path is not None:
        spec["path"] = path
    if data_base64 is not None:
        spec["data_base64"] = data_base64
    if name is not None:
        spec["name"] = name
    if mime_type is not None:
        spec["mime_type"] = mime_type
    return [spec]


def route_media_kind(mime_type: str) -> str:
    """Pick the Telegram delivery method for a mime type so media plays natively."""

    if mime_type.startswith("image/"):
        return "photo"
    if mime_type.startswith("video/"):
        return "video"
    if mime_type.startswith("audio/"):
        return "audio"
    return "document"


async def deliver_attachment(
    *,
    bot: Bot,
    chat_id: int,
    payload: AttachmentPayload,
    resolved_mime: str,
) -> str:
    """Send one loaded attachment with the method that matches its mime type."""

    input_file = InputFile(BytesIO(payload.raw), filename=payload.filename)
    match route_media_kind(resolved_mime):
        case "photo":
            await bot.send_photo(chat_id=chat_id, photo=input_file)
        case "video":
            await bot.send_video(chat_id=chat_id, video=input_file)
        case "audio":
            await bot.send_audio(chat_id=chat_id, audio=input_file)
        case _:
            await bot.send_document(chat_id=chat_id, document=input_file)
    return route_media_kind(resolved_mime)


def load_attachment_bytes(
    *,
    path: str | None,
    data_base64: str | None,
    name: str | None,
) -> AttachmentPayload | str:
    """Load attachment bytes from a trusted path or base64 payload."""

    if (path is None) == (data_base64 is None):
        return EXACTLY_ONE_ATTACHMENT_SOURCE_ERROR

    if path is not None:
        source_path = Path(path).expanduser().resolve(strict=False)
        if not source_path.is_file():
            return f"file not found: {source_path}"
        raw = source_path.read_bytes()
        filename = name or source_path.name
        guessed_mime = mimetypes.guess_type(filename)[0]
        return AttachmentPayload(raw=raw, filename=filename, guessed_mime=guessed_mime)

    try:
        raw = base64.b64decode(cast(str, data_base64), validate=True)
    except (ValueError, binascii.Error):
        return "invalid base64 payload"
    filename = name or "attachment.bin"
    guessed_mime = mimetypes.guess_type(filename)[0]
    return AttachmentPayload(raw=raw, filename=filename, guessed_mime=guessed_mime)


def allow_path_inputs() -> bool:
    """Return whether trusted `path` inputs are enabled for attachment delivery."""

    value = os.getenv(ALLOW_PATH_ENV, "").strip().lower()
    return value in {"1", "true", "yes", "on"}
