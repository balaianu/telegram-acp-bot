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

from telegram_acp_bot.mcp.context import resolve_request_context

ALLOW_PATH_ENV = "ACP_TELEGRAM_CHANNEL_ALLOW_PATH"
EXACTLY_ONE_ATTACHMENT_SOURCE_ERROR = "provide exactly one of `path` or `data_base64`"
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
            "Send an attachment to the current Telegram chat. "
            "If session_id is omitted, the server auto-resolves it when possible. "
            "Use this when the user asks to send an image/file."
        ),
    )(telegram_send_attachment)

    mcp.tool(
        name="telegram_fetch_attachment",
        description=(
            "Download one Telegram attachment by file_id into the agent working directory and return its path. "
            "Use this for media that was announced as pending instead of being delivered inline: read it only "
            "when the conversation actually needs the pixels or the file contents. Pass the announced mime_type "
            "so the saved file gets a usable extension, or pass dest to choose the path yourself."
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


def resolve_media_target(*, dest: str | None, file_id: str, mime_type: str | None = None) -> Path | str:
    """Resolve where a fetched attachment may be written, keeping it inside the working directory."""

    root = Path.cwd().resolve(strict=False)
    if dest is None:
        return root / DEFAULT_MEDIA_DIRNAME / default_media_name(file_id, mime_type)

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


async def telegram_send_attachment(
    session_id: str | None = None,
    path: str | None = None,
    data_base64: str | None = None,
    name: str | None = None,
    mime_type: str | None = None,
) -> dict[str, object]:
    def fail(error: str) -> dict[str, object]:
        return {"ok": False, "error": error}

    if path is not None and not allow_path_inputs():
        return fail(f"`path` input is disabled by default. Set {ALLOW_PATH_ENV}=1 to enable it.")

    context = resolve_request_context(session_id=session_id)
    if isinstance(context, str):
        return fail(context)

    loaded = load_attachment_bytes(path=path, data_base64=data_base64, name=name)
    if isinstance(loaded, str):
        return fail(loaded)

    resolved_mime = mime_type or loaded.guessed_mime or "application/octet-stream"
    bot = Bot(token=context.token)
    input_file = InputFile(BytesIO(loaded.raw), filename=loaded.filename)
    if resolved_mime.startswith("image/"):
        await bot.send_photo(chat_id=context.chat_id, photo=input_file)
        delivered_as = "photo"
    else:
        await bot.send_document(chat_id=context.chat_id, document=input_file)
        delivered_as = "document"

    return {
        "ok": True,
        "session_id": context.session_id,
        "chat_id": context.chat_id,
        "delivered_as": delivered_as,
        "name": loaded.filename,
        "mime_type": resolved_mime,
    }


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
