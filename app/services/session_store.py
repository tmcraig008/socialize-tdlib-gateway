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


def _tdlib_send_meta(settings: Any) -> dict[str, Any]:
    """Expose whether outbound traffic is real TDLib or mock simulation (Socialize must not treat mock as delivered)."""
    mode = str(getattr(settings, "tdlib_mode", "mock") or "mock").strip().lower()
    return {
        "tdlibMode": getattr(settings, "tdlib_mode", None) or "mock",
        "simulated": mode == "mock",
    }


def _is_mock_mode(settings: Any) -> bool:
    return str(getattr(settings, "tdlib_mode", "mock") or "mock").strip().lower() == "mock"


async def start_workspace(workspace_id: str, phone: str | None) -> dict[str, Any]:
    s = get_session(workspace_id)
    settings = get_settings()
    async with s._lock:
        if phone:
            s.phone = phone
        if settings.tdlib_mode == "mock":
            s.status = "pending_auth"
            s.account_id = s.account_id or f"mock-{workspace_id[:8]}"
            await socialize_webhook.notify_account_status(
                workspace_id, s.status, account_id=s.account_id, phone=s.phone
            )
            return {"status": s.status, "accountId": s.account_id}

        from app.services import tdlib_runtime as rt

        await rt.ensure_client(workspace_id)
        if phone:
            await rt.submit_phone(workspace_id, phone)
        live = await rt.workspace_live_status_async(workspace_id)
        s.status = live.get("status", "pending_auth")
        s.account_id = live.get("accountId")
        await socialize_webhook.notify_account_status(
            workspace_id, s.status, account_id=s.account_id, phone=s.phone
        )
        return {"status": s.status, "accountId": s.account_id}


async def stop_workspace(workspace_id: str) -> dict[str, Any]:
    s = get_session(workspace_id)
    settings = get_settings()
    async with s._lock:
        if settings.tdlib_mode != "mock":
            from app.services import tdlib_runtime as rt

            await rt.remove_client(workspace_id)
        s.status = "disconnected"
        s.account_id = None
        s.qr_token = None
        await socialize_webhook.notify_account_status(workspace_id, "disconnected", phone=s.phone)
        return {"status": "disconnected"}


async def workspace_status(workspace_id: str) -> dict[str, Any]:
    s = get_session(workspace_id)
    settings = get_settings()
    async with s._lock:
        if settings.tdlib_mode == "mock":
            return {
                "status": s.status,
                "accountId": s.account_id,
                "phone": s.phone,
            }
        from app.services import tdlib_runtime as rt

        live = await rt.workspace_live_status_async(workspace_id)
        if live.get("accountId"):
            s.account_id = live["accountId"]
        s.status = live.get("status", s.status)
        return {
            "status": live.get("status", s.status),
            "accountId": live.get("accountId") or s.account_id,
            "phone": s.phone,
        }


async def auth_phone(workspace_id: str, phone: str) -> dict[str, Any]:
    s = get_session(workspace_id)
    settings = get_settings()
    async with s._lock:
        s.phone = phone
        if settings.tdlib_mode == "mock":
            s.status = "pending_auth"
            await socialize_webhook.notify_account_status(
                workspace_id, s.status, account_id=s.account_id, phone=s.phone
            )
            return {"status": s.status}

        from app.services import tdlib_runtime as rt

        await rt.submit_phone(workspace_id, phone)
        live = await rt.workspace_live_status_async(workspace_id)
        s.status = live.get("status", "pending_auth")
        s.account_id = live.get("accountId") or s.account_id
        await socialize_webhook.notify_account_status(
            workspace_id, s.status, account_id=s.account_id, phone=s.phone
        )
        return {"status": s.status}


async def auth_code(workspace_id: str, code: str) -> dict[str, Any]:
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

        from app.services import tdlib_runtime as rt

        await rt.submit_code(workspace_id, code)
        live = await rt.workspace_live_status_async(workspace_id)
        s.status = live.get("status", "pending_auth")
        s.account_id = live.get("accountId") or s.account_id
        await socialize_webhook.notify_account_status(
            workspace_id, s.status, account_id=s.account_id, phone=s.phone
        )
        return {"status": s.status}


async def auth_password(workspace_id: str, password: str) -> dict[str, Any]:
    s = get_session(workspace_id)
    settings = get_settings()
    async with s._lock:
        if settings.tdlib_mode == "mock":
            s.status = "connected"
            await socialize_webhook.notify_account_status(
                workspace_id, "connected", account_id=s.account_id, phone=s.phone
            )
            return {"status": "connected"}

        from app.services import tdlib_runtime as rt

        await rt.submit_password(workspace_id, password)
        live = await rt.workspace_live_status_async(workspace_id)
        s.status = live.get("status", "pending_auth")
        s.account_id = live.get("accountId") or s.account_id
        await socialize_webhook.notify_account_status(
            workspace_id, s.status, account_id=s.account_id, phone=s.phone
        )
        return {"status": s.status}


async def auth_qr_create(workspace_id: str) -> dict[str, Any]:
    s = get_session(workspace_id)
    settings = get_settings()
    async with s._lock:
        if settings.tdlib_mode == "mock":
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

        from app.services import tdlib_runtime as rt

        out = await rt.request_qr_link(workspace_id)
        s.qr_token = out.get("token")
        s.status = out.get("status", "pending_auth")
        s.qr_expires_at = time.time() + 120
        await socialize_webhook.notify_account_status(workspace_id, s.status, phone=s.phone)
        return {
            "token": out.get("token"),
            "qrData": out.get("qrData"),
            "status": s.status,
        }


async def auth_qr_status(workspace_id: str, token: str) -> dict[str, Any]:
    s = get_session(workspace_id)
    settings = get_settings()
    async with s._lock:
        if settings.tdlib_mode == "mock":
            if s.qr_token == token and time.time() < s.qr_expires_at + 3600:
                s.status = "connected"
                s.account_id = s.account_id or f"mock-{workspace_id[:8]}"
                await socialize_webhook.notify_account_status(
                    workspace_id, "connected", account_id=s.account_id, phone=s.phone
                )
                return {"status": "connected"}
            if s.qr_token != token:
                return {"status": "pending_auth"}
            return {"status": s.status}

        from app.services import tdlib_runtime as rt

        out = await rt.qr_status(workspace_id, token)
        st = out.get("status", "pending_auth")
        s.status = st
        if st == "connected":
            live = await rt.workspace_live_status_async(workspace_id)
            s.account_id = live.get("accountId") or s.account_id
            await socialize_webhook.notify_account_status(
                workspace_id, "connected", account_id=s.account_id, phone=s.phone
            )
        return {"status": st}


async def send_text(
    workspace_id: str,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None,
    link_label: str | None = None,
    link_url: str | None = None,
) -> dict[str, Any]:
    _ = reply_to_message_id
    settings = get_settings()
    s = get_session(workspace_id)
    async with s._lock:
        if s.status != "connected" and _is_mock_mode(settings):
            s.status = "connected"
    if _is_mock_mode(settings):
        return {"telegramMessageId": _next_message_id(), **_tdlib_send_meta(settings)}
    try:
        from app.services.tdlib_live import send_message_live

        mid = await send_message_live(
            workspace_id,
            chat_id,
            text,
            reply_to_message_id,
            link_label=link_label,
            link_url=link_url,
        )
        return {"telegramMessageId": mid, **_tdlib_send_meta(settings)}
    except NotImplementedError as e:
        raise RuntimeError(str(e)) from e


async def edit_text(
    workspace_id: str,
    chat_id: int,
    message_id: int,
    text: str,
    link_label: str | None = None,
    link_url: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    s = get_session(workspace_id)
    async with s._lock:
        if s.status != "connected" and _is_mock_mode(settings):
            s.status = "connected"
    if _is_mock_mode(settings):
        return {"ok": True, **_tdlib_send_meta(settings)}
    try:
        from app.services.tdlib_live import edit_message_text_live

        await edit_message_text_live(
            workspace_id,
            chat_id,
            message_id,
            text,
            link_label=link_label,
            link_url=link_url,
        )
        return {"ok": True, **_tdlib_send_meta(settings)}
    except NotImplementedError as e:
        raise RuntimeError(str(e)) from e


async def send_media(
    workspace_id: str,
    chat_id: int,
    path: str,
    kind: str,
    caption: str | None,
    link_label: str | None = None,
    link_url: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if _is_mock_mode(settings):
        return {"telegramMessageId": _next_message_id(), **_tdlib_send_meta(settings)}
    try:
        from app.services.tdlib_live import send_media_live

        mid = await send_media_live(
            workspace_id,
            chat_id,
            path,
            kind,
            caption,
            link_label=link_label,
            link_url=link_url,
        )
        return {"telegramMessageId": mid, **_tdlib_send_meta(settings)}
    except NotImplementedError as e:
        raise RuntimeError(str(e)) from e
