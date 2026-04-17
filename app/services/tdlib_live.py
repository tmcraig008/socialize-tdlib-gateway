"""
Real TDLib sends for TDLIB_MODE=live (pytdbot).
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urljoin

import httpx
from pytdbot import types

from app.config import get_settings
from app.services.tdlib_runtime import get_client, reattach_tdlib_client_if_persisted

log = logging.getLogger(__name__)

# TDLib reads InputFileLocal asynchronously after sendMessage returns; deleting the temp file
# in a finally block races the upload and recipients often never receive the media.
_TEMP_UNLINK_DELAY_SEC = 600.0


async def _unlink_after_delay(path: str, delay_sec: float) -> None:
    await asyncio.sleep(delay_sec)
    try:
        os.unlink(path)
    except OSError:
        pass


def _schedule_temp_file_delete(path: str, delay_sec: float = _TEMP_UNLINK_DELAY_SEC) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(_unlink_after_delay(path, delay_sec))


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


def _utf16_len(s: str) -> int:
    """TDLib TextEntity offset/length are in UTF-16 code units."""
    return len(s.encode("utf-16-le")) // 2


def _formatted_text_plain(text: str) -> types.FormattedText:
    return types.FormattedText(text=text or "")


def _formatted_text_with_link(text_before: str, link_label: str, url: str) -> types.FormattedText:
    tb = text_before or ""
    if tb:
        spacer = "\n\n"
        combined = tb + spacer + link_label
        off = _utf16_len(tb + spacer)
    else:
        combined = link_label
        off = 0
    ln = _utf16_len(link_label)
    ent = types.TextEntity(
        offset=off,
        length=ln,
        type=types.TextEntityTypeTextUrl(url=url),
    )
    return types.FormattedText(text=combined, entities=[ent])


def _reply_to(reply_to_message_id: int | None) -> types.InputMessageReplyToMessage | None:
    if not reply_to_message_id:
        return None
    return types.InputMessageReplyToMessage(message_id=reply_to_message_id)


async def _resolve_chat_id_for_send(c, target_chat_id: int) -> int:
    """
    Socialize usually stores Telegram *user* ids (Bot API private chat id == user id).
    TDLib user clients need the real private *chat* id from getChat/createPrivateChat.
    If the caller already passes a TDLib chat id, getChat succeeds and we keep it.
    """
    if target_chat_id == 0:
        return target_chat_id
    if target_chat_id < 0:
        return target_chat_id

    errs: list[str] = []

    existing = await c.getChat(chat_id=target_chat_id)
    if not isinstance(existing, types.Error):
        cid = int(getattr(existing, "id", 0) or 0)
        if cid:
            log.debug("TDLib send: %s is already a chat id", target_chat_id)
            return cid
    else:
        errs.append(f"getChat: {existing.message} ({existing.code})")

    for force in (False, True):
        chat = await c.createPrivateChat(user_id=target_chat_id, force=force)
        if isinstance(chat, types.Error):
            errs.append(f"createPrivateChat(force={force}): {chat.message} ({chat.code})")
            continue
        cid = int(getattr(chat, "id", 0) or 0)
        if cid:
            log.info(
                "TDLib send: resolved user_id %s -> chat_id %s (force=%s)",
                target_chat_id,
                cid,
                force,
            )
            return cid
        errs.append(f"createPrivateChat(force={force}): chat missing id")

    raise RuntimeError(
        f"Could not open a private TDLib chat for Telegram id {target_chat_id}. "
        f"TDLib: {'; '.join(errs)}. "
        "If this user has never messaged this account, they may need to start the chat first, "
        "or their privacy settings may block non-contacts."
    )


async def _send_with_private_chat_fallback(
    c,
    target_chat_id: int,
    *,
    reply_to,
    input_message_content,
):
    resolved_chat_id = await _resolve_chat_id_for_send(c, target_chat_id)
    sent = await c.sendMessage(
        chat_id=resolved_chat_id,
        reply_to=reply_to,
        input_message_content=input_message_content,
    )
    if isinstance(sent, types.Error) and resolved_chat_id != target_chat_id:
        # Retry with original id in case the input was already a real chat id.
        sent = await c.sendMessage(
            chat_id=target_chat_id,
            reply_to=reply_to,
            input_message_content=input_message_content,
        )
    return sent


async def send_message_live(
    workspace_id: str,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None,
    link_label: str | None = None,
    link_url: str | None = None,
) -> int:
    await reattach_tdlib_client_if_persisted(workspace_id)
    c = _require_ready_client(workspace_id)
    if link_label and link_url:
        body = _formatted_text_with_link(text, link_label.strip(), link_url.strip())
    else:
        body = _formatted_text_plain(text)
    content = types.InputMessageText(
        text=body,
        clear_draft=True,
    )
    sent = await _send_with_private_chat_fallback(
        c,
        chat_id,
        reply_to=_reply_to(reply_to_message_id),
        input_message_content=content,
    )
    if isinstance(sent, types.Error):
        raise RuntimeError(sent.message)
    return int(sent.id)


async def edit_message_text_live(
    workspace_id: str,
    chat_id: int,
    message_id: int,
    text: str,
    link_label: str | None = None,
    link_url: str | None = None,
) -> None:
    """Edit an existing text message in a private chat (same chat resolution as send)."""
    await reattach_tdlib_client_if_persisted(workspace_id)
    c = _require_ready_client(workspace_id)
    if link_label and link_url:
        body = _formatted_text_with_link(text, link_label.strip(), link_url.strip())
        plain_text = body.text
    else:
        body = _formatted_text_plain(text)
        plain_text = text
    content = types.InputMessageText(
        text=body,
        clear_draft=True,
    )
    resolved_chat_id = await _resolve_chat_id_for_send(c, chat_id)
    result = await c.editMessageText(
        chat_id=resolved_chat_id,
        message_id=message_id,
        input_message_content=content,
    )
    if isinstance(result, types.Error) and resolved_chat_id != chat_id:
        result = await c.editMessageText(
            chat_id=chat_id,
            message_id=message_id,
            input_message_content=content,
        )
    if isinstance(result, types.Error):
        # Fallback for TDLib stacks where editMessageText rejects but text helper succeeds.
        result = await c.editTextMessage(
            chat_id=resolved_chat_id,
            message_id=message_id,
            text=plain_text,
        )
    if isinstance(result, types.Error):
        raise RuntimeError(result.message)


async def send_media_live(
    workspace_id: str,
    chat_id: int,
    path: str,
    kind: str,
    caption: str | None,
    link_label: str | None = None,
    link_url: str | None = None,
) -> int:
    await reattach_tdlib_client_if_persisted(workspace_id)
    c = _require_ready_client(workspace_id)
    local_path, temp = await _resolve_local_path(path, kind)
    td_path = tdlib_local_path_str(local_path)
    cap: types.FormattedText | None
    raw_cap = (caption or "").strip()
    if link_label and link_url:
        cap = _formatted_text_with_link(raw_cap, link_label.strip(), link_url.strip())
    elif raw_cap:
        cap = _formatted_text_plain(raw_cap)
    else:
        cap = None
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
        sent = await _send_with_private_chat_fallback(
            c,
            chat_id,
            reply_to=None,
            input_message_content=content,
        )
        if isinstance(sent, types.Error):
            raise RuntimeError(sent.message)
        mid = int(sent.id)
        if temp:
            _schedule_temp_file_delete(local_path)
            log.info(
                "TDLib media queued (msg_id=%s); temp file delete scheduled in %ss: %s",
                mid,
                int(_TEMP_UNLINK_DELAY_SEC),
                local_path,
            )
        return mid
    except Exception:
        if temp:
            try:
                os.unlink(local_path)
            except OSError:
                pass
        raise


async def on_incoming_update_for_workspace(workspace_id: str, update: object) -> None:
    _ = workspace_id, update
