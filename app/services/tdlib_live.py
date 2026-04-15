"""
Real TDLib sends for TDLIB_MODE=live (pytdbot).
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urljoin

import httpx
from pytdbot import types

from app.config import get_settings
from app.services.tdlib_runtime import get_client


def tdlib_local_path_str(path: str) -> str:
    return str(Path(path).resolve()).replace("\\", "/")


async def _download_url_to_temp(url: str, suffix: str) -> str:
    async with httpx.AsyncClient(timeout=120.0, follow_redirects=True) as client:
        r = await client.get(url)
        r.raise_for_status()
        data = r.content
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        tmp.write(data)
        tmp.close()
        return tmp.name
    except Exception:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        raise


def _suffix_from_url_or_kind(url_or_path: str, kind: str) -> str:
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        path = urlparse(url_or_path).path
        suf = Path(path).suffix.lower()
        if suf:
            return suf
        if kind == "photo":
            return ".jpg"
        if kind == "video":
            return ".mp4"
        if kind == "audio":
            return ".ogg"
        return ".bin"
    if kind == "photo":
        return ".jpg"
    if kind == "video":
        return ".mp4"
    if kind == "audio":
        return ".ogg"
    return ".bin"


async def _resolve_local_path(path_or_url: str, kind: str) -> tuple[str, bool]:
    """Returns (local_path, should_unlink)."""
    raw = (path_or_url or "").strip()
    if raw.startswith("http://") or raw.startswith("https://"):
        suf = _suffix_from_url_or_kind(raw, kind)
        tmp = await _download_url_to_temp(raw, suf)
        return tmp, True
    # Socialize often sends attachment URLs as app-relative paths like /uploads/...
    if raw.startswith("/"):
        base = get_settings().socialize_backend_url.rstrip("/") + "/"
        full = urljoin(base, raw.lstrip("/"))
        suf = _suffix_from_url_or_kind(full, kind)
        tmp = await _download_url_to_temp(full, suf)
        return tmp, True
    if raw and Path(raw).exists():
        return raw, False
    raise RuntimeError(
        f"Media path is not reachable for TDLib send: {path_or_url!r}. "
        "Use an http(s) URL, an app-relative /uploads/... URL, or a valid local file path."
    )


def _require_ready_client(workspace_id: str):
    c = get_client(workspace_id)
    if not c:
        raise RuntimeError("No TDLib session for this workspace; sign in first.")
    if getattr(c, "authorization_state", None) != "authorizationStateReady":
        raise RuntimeError(
            f"TDLib not connected (state={getattr(c, 'authorization_state', None)!r})."
        )
    return c


def _reply_to(reply_to_message_id: int | None) -> types.InputMessageReplyToMessage | None:
    if not reply_to_message_id:
        return None
    return types.InputMessageReplyToMessage(message_id=reply_to_message_id)


async def send_message_live(
    workspace_id: str,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None,
) -> int:
    c = _require_ready_client(workspace_id)
    content = types.InputMessageText(
        text=types.FormattedText(text=text),
        clear_draft=True,
    )
    sent = await c.sendMessage(
        chat_id=chat_id,
        reply_to=_reply_to(reply_to_message_id),
        input_message_content=content,
    )
    if isinstance(sent, types.Error):
        raise RuntimeError(sent.message)
    return int(sent.id)


async def send_media_live(
    workspace_id: str,
    chat_id: int,
    path: str,
    kind: str,
    caption: str | None,
) -> int:
    c = _require_ready_client(workspace_id)
    local_path, temp = await _resolve_local_path(path, kind)
    td_path = tdlib_local_path_str(local_path)
    cap = types.FormattedText(text=caption.strip()) if caption and caption.strip() else None
    try:
        k = kind.lower()
        if k == "photo":
            content = types.InputMessagePhoto(
                photo=types.InputFileLocal(path=td_path),
                caption=cap,
            )
        elif k == "video":
            content = types.InputMessageVideo(
                video=types.InputFileLocal(path=td_path),
                supports_streaming=True,
                caption=cap,
            )
        elif k == "audio":
            content = types.InputMessageAudio(
                audio=types.InputFileLocal(path=td_path),
                caption=cap,
            )
        else:
            content = types.InputMessageDocument(
                document=types.InputFileLocal(path=td_path),
                caption=cap,
            )
        sent = await c.sendMessage(chat_id=chat_id, input_message_content=content)
        if isinstance(sent, types.Error):
            raise RuntimeError(sent.message)
        return int(sent.id)
    finally:
        if temp:
            try:
                os.unlink(local_path)
            except OSError:
                pass


async def on_incoming_update_for_workspace(workspace_id: str, update: object) -> None:
    _ = workspace_id, update
