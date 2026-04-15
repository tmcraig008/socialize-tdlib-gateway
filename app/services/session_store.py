from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import Any

from app.config import get_settings
from app.services import socialize_webhook

TdlibStatus = str


@dataclass
class WorkspaceSession:
    workspace_id: str
    status: TdlibStatus = "disconnected"
    phone: str | None = None
    account_id: str | None = None
    qr_token: str | None = None
    qr_expires_at: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)


_sessions: dict[str, WorkspaceSession] = {}


def get_session(workspace_id: str) -> WorkspaceSession:
    if workspace_id not in _sessions:
        _sessions[workspace_id] = WorkspaceSession(workspace_id=workspace_id)
    return _sessions[workspace_id]


def _next_message_id() -> int:
    return int(time.time() * 1000) % 1_000_000_000 + random.randint(0, 999)


async def start_workspace(workspace_id: str, phone: str | None) -> dict[str, Any]:
    s = get_session(workspace_id)
    async with s._lock:
        settings = get_settings()
        if phone:
            s.phone = phone
        if settings.tdlib_mode == "mock":
            s.status = "pending_auth"
            s.account_id = s.account_id or f"mock-{workspace_id[:8]}"
            await socialize_webhook.notify_account_status(
                workspace_id, s.status, account_id=s.account_id, phone=s.phone
            )
            return {"status": s.status, "accountId": s.account_id}
        s.status = "pending_auth"
        await socialize_webhook.notify_account_status(
            workspace_id, s.status, account_id=s.account_id, phone=s.phone
        )
        return {"status": s.status, "accountId": s.account_id}


async def stop_workspace(workspace_id: str) -> dict[str, Any]:
    s = get_session(workspace_id)
    async with s._lock:
        s.status = "disconnected"
        await socialize_webhook.notify_account_status(workspace_id, "disconnected", phone=s.phone)
        return {"status": "disconnected"}


async def workspace_status(workspace_id: str) -> dict[str, Any]:
    s = get_session(workspace_id)
    async with s._lock:
        return {
            "status": s.status,
            "accountId": s.account_id,
            "phone": s.phone,
        }


async def auth_phone(workspace_id: str, phone: str) -> dict[str, Any]:
    s = get_session(workspace_id)
    async with s._lock:
        s.phone = phone
        s.status = "pending_auth"
        await socialize_webhook.notify_account_status(
            workspace_id, s.status, account_id=s.account_id, phone=s.phone
        )
        return {"status": s.status}


async def auth_code(workspace_id: str, code: str) -> dict[str, Any]:
    _ = code
    s = get_session(workspace_id)
    settings = get_settings()
    async with s._lock:
        if settings.tdlib_mode == "mock":
            s.status = "connected"
            s.account_id = s.account_id or f"mock-{workspace_id[:8]}"
            await socialize_webhook.notify_account_status(
                workspace_id, "connected", account_id=s.account_id, phone=s.phone
            )
            return {"status": "connected"}
        s.status = "pending_auth"
        return {"status": s.status}


async def auth_password(workspace_id: str, password: str) -> dict[str, Any]:
    _ = password
    s = get_session(workspace_id)
    settings = get_settings()
    async with s._lock:
        if settings.tdlib_mode == "mock":
            s.status = "connected"
            await socialize_webhook.notify_account_status(
                workspace_id, "connected", account_id=s.account_id, phone=s.phone
            )
            return {"status": "connected"}
        return {"status": s.status}


async def auth_qr_create(workspace_id: str) -> dict[str, Any]:
    s = get_session(workspace_id)
    async with s._lock:
        token = f"qr-{workspace_id[:8]}-{int(time.time())}"
        s.qr_token = token
        s.qr_expires_at = time.time() + 600
        s.status = "pending_auth"
        await socialize_webhook.notify_account_status(workspace_id, s.status, phone=s.phone)
        return {
            "token": token,
            "qrData": f"tg://login?token={token}",
            "status": s.status,
        }


async def auth_qr_status(workspace_id: str, token: str) -> dict[str, Any]:
    s = get_session(workspace_id)
    settings = get_settings()
    async with s._lock:
        if settings.tdlib_mode == "mock" and s.qr_token == token and time.time() < s.qr_expires_at + 3600:
            s.status = "connected"
            s.account_id = s.account_id or f"mock-{workspace_id[:8]}"
            await socialize_webhook.notify_account_status(
                workspace_id, "connected", account_id=s.account_id, phone=s.phone
            )
            return {"status": "connected"}
        if s.qr_token != token:
            return {"status": "pending_auth"}
        return {"status": s.status}


async def send_text(
    workspace_id: str,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None,
) -> dict[str, Any]:
    _ = reply_to_message_id
    settings = get_settings()
    s = get_session(workspace_id)
    async with s._lock:
        if s.status != "connected" and settings.tdlib_mode == "mock":
            s.status = "connected"
    if settings.tdlib_mode == "mock":
        return {"telegramMessageId": _next_message_id()}
    try:
        from app.services.tdlib_live import send_message_live

        mid = await send_message_live(workspace_id, chat_id, text, reply_to_message_id)
        return {"telegramMessageId": mid}
    except NotImplementedError as e:
        raise RuntimeError(str(e)) from e


async def send_media(
    workspace_id: str,
    chat_id: int,
    path: str,
    kind: str,
    caption: str | None,
) -> dict[str, Any]:
    settings = get_settings()
    if settings.tdlib_mode == "mock":
        return {"telegramMessageId": _next_message_id()}
    try:
        from app.services.tdlib_live import send_media_live

        mid = await send_media_live(workspace_id, chat_id, path, kind, caption)
        return {"telegramMessageId": mid}
    except NotImplementedError as e:
        raise RuntimeError(str(e)) from e
