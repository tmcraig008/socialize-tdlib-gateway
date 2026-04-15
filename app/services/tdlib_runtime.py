"""
Per-workspace TDLib clients (pytdbot) for TDLIB_MODE=live.

Auth + QR flow matches the standalone "TG no Bot" project: phone queue, literal "qr"
→ requestQrCodeAuthentication(), QR link from AuthorizationStateWaitOtherDeviceConfirmation.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

from filelock import FileLock
from pytdbot import Client, ClientManager, types

from app.config import get_settings
from app.services import socialize_webhook
from app.services.pytdbot_tdlib_compat import install_pytdbot_schema_fallback

install_pytdbot_schema_fallback()

log = logging.getLogger(__name__)

# --- Auth bridge (same idea as TG no Bot/tg_client.py) ---


class AuthBridge:
    def __init__(self) -> None:
        self.state_name: str = "unknown"
        self.code_hint: str = ""
        self.password_hint: str = ""
        self.qr_link: Optional[str] = None
        self.closed_reason: str = ""
        self.last_tdlib_error: str = ""
        self.ready_event = asyncio.Event()
        self._phone_q: asyncio.Queue[str] = asyncio.Queue()
        self._code_q: asyncio.Queue[str] = asyncio.Queue()
        self._password_q: asyncio.Queue[str] = asyncio.Queue()

    def reset_session_hints(self) -> None:
        self.code_hint = ""
        self.password_hint = ""
        self.qr_link = None
        self.closed_reason = ""

    async def put_phone(self, value: str) -> None:
        await self._phone_q.put(value)

    async def put_code(self, value: str) -> None:
        await self._code_q.put(value)

    async def put_password(self, value: str) -> None:
        await self._password_q.put(value)

    async def next_phone(self) -> str:
        return await self._phone_q.get()

    async def next_code(self) -> str:
        return await self._code_q.get()

    async def next_password(self) -> str:
        return await self._password_q.get()


_td_parameter_hook_installed = False


def _install_set_tdlib_parameters_hook() -> None:
    global _td_parameter_hook_installed
    if _td_parameter_hook_installed:
        return
    from pytdbot.client import Client as PytdbotClient
    from pytdbot.exception import AuthorizationError

    _orig = PytdbotClient.set_td_parameters

    async def _wrapped(self: PytdbotClient) -> None:
        try:
            await _orig(self)
        except AuthorizationError as e:
            b = getattr(self, "_auth_bridge", None)
            if b is not None:
                b.last_tdlib_error = str(e)
            log.error("setTdlibParameters / DB open failed: %s", e)
            raise

    PytdbotClient.set_td_parameters = _wrapped
    _td_parameter_hook_installed = True


def describe_code_type(code_type: types.AuthenticationCodeType) -> str:
    if isinstance(code_type, types.AuthenticationCodeTypeTelegramMessage):
        return "Telegram app"
    if isinstance(code_type, types.AuthenticationCodeTypeSms):
        return "SMS"
    if isinstance(
        code_type,
        (types.AuthenticationCodeTypeSmsWord, types.AuthenticationCodeTypeSmsPhrase),
    ):
        kind = "Word" if isinstance(code_type, types.AuthenticationCodeTypeSmsWord) else "Phrase"
        extra = "" if not code_type.first_letter else f" (first letter: {code_type.first_letter})"
        return f"SMS {kind}{extra}"
    if isinstance(code_type, types.AuthenticationCodeTypeCall):
        return "Call"
    if isinstance(code_type, types.AuthenticationCodeTypeFlashCall):
        return "Flash call"
    if isinstance(code_type, types.AuthenticationCodeTypeMissedCall):
        return (
            f"Missed call (prefix: {code_type.phone_number_prefix}, "
            f"length: {code_type.phone_number_length})"
        )
    if isinstance(code_type, types.AuthenticationCodeTypeFragment):
        return f"Fragment (url: {code_type.url})"
    return "Unknown"


def _token_from_tg_login_url(link: str) -> str:
    m = re.search(r"[?&]token=([^&]+)", link.strip())
    if not m:
        return link
    try:
        from urllib.parse import unquote

        return unquote(m.group(1))
    except Exception:
        return m.group(1)


def _map_authorization_to_status(auth_state: str | None) -> str:
    if auth_state == "authorizationStateReady":
        return "connected"
    if auth_state in (None, "authorizationStateClosed"):
        return "disconnected"
    return "pending_auth"


async def _notify_status(workspace_id: str, status: str, account_id: str | None, phone: str | None) -> None:
    try:
        await socialize_webhook.notify_account_status(workspace_id, status, account_id=account_id, phone=phone)
    except Exception:
        log.exception("notify_account_status failed for %s", workspace_id)


async def _message_to_webhook_payload(
    workspace_id: str, client: Client, message: types.Message
) -> dict[str, Any] | None:
    if message.is_outgoing:
        return None
    chat_id = message.chat_id
    text = ""
    caption = ""
    media_type: str | None = None
    c = message.content
    if isinstance(c, types.MessageText) and c.text:
        text = c.text.text or ""
    elif isinstance(c, types.MessagePhoto):
        media_type = "photo"
        if c.caption and c.caption.text:
            caption = c.caption.text or ""
    elif isinstance(c, types.MessageVideo):
        media_type = "video"
        if c.caption and c.caption.text:
            caption = c.caption.text or ""
    elif isinstance(c, types.MessageDocument):
        media_type = "document"
        if c.caption and c.caption.text:
            caption = c.caption.text or ""
    elif isinstance(c, types.MessageVoiceNote):
        media_type = "audio"
    else:
        try:
            text = message.text or ""
        except Exception:
            text = ""

    sid = message.sender_id
    sender: dict[str, Any] = {"id": chat_id, "username": None, "firstName": None, "lastName": None}
    if isinstance(sid, types.MessageSenderUser):
        sender["id"] = sid.user_id
        try:
            u = await client.getUser(sid.user_id)
            if not isinstance(u, types.Error):
                sender["username"] = getattr(u, "username", None)
                sender["firstName"] = getattr(u, "first_name", None)
                sender["lastName"] = getattr(u, "last_name", None)
        except Exception:
            # Keep webhook robust even if user lookup fails.
            pass
    body: dict[str, Any] = {
        "chatId": chat_id,
        "messageId": message.id,
        "text": text or None,
        "caption": caption or None,
        "sender": sender,
        "mediaType": media_type,
        "mediaUrl": None,
    }
    _ = workspace_id
    return body


def _schedule_incoming_webhook(workspace_id: str, client: Client, message: types.Message) -> None:
    """
    Never await TDLib methods (e.g. getUser) directly inside on_message: pytdbot processes
    updates on the same loop; awaiting another request there can deadlock and stop all messages.
    """

    async def _run() -> None:
        try:
            payload = await _message_to_webhook_payload(workspace_id, client, message)
            if payload:
                await socialize_webhook.notify_incoming_message(workspace_id, payload)
        except Exception:
            log.exception("incoming message webhook failed")

    try:
        asyncio.get_running_loop().create_task(_run())
    except RuntimeError:
        log.exception("no running loop for incoming webhook")


def register_handlers(c: Client, bridge: AuthBridge, workspace_id: str) -> None:
    @c.on_message()
    async def on_incoming(_: Client, message: types.Message):
        if message.is_outgoing:
            return
        _schedule_incoming_webhook(workspace_id, c, message)

    @c.on_updateAuthorizationState()
    async def on_auth(_: Client, auth: types.UpdateAuthorizationState):
        state = auth.authorization_state
        bridge.reset_session_hints()

        if isinstance(state, types.AuthorizationStateClosed):
            bridge.state_name = "closed"
            bridge.ready_event.clear()
            bridge.closed_reason = bridge.last_tdlib_error or "authorizationStateClosed"
            await _notify_status(workspace_id, "disconnected", None, None)
            log.error("authorizationStateClosed workspace=%s", workspace_id)
            return

        if isinstance(state, types.AuthorizationStateClosing):
            bridge.state_name = "closing"
            return

        if isinstance(state, types.AuthorizationStateLoggingOut):
            bridge.state_name = "loggingOut"
            return

        if isinstance(state, types.AuthorizationStateWaitPhoneNumber):
            bridge.state_name = "waitPhoneNumber"
            bridge.ready_event.clear()
            bridge.last_tdlib_error = ""
            while True:
                user_input = (await bridge.next_phone()).strip()
                if not user_input:
                    continue
                if user_input.lower() == "qr":
                    res = await c.requestQrCodeAuthentication()
                else:
                    res = await c.setAuthenticationPhoneNumber(phone_number=user_input)
                if isinstance(res, types.Error):
                    log.warning("Phone/QR step workspace=%s: %s", workspace_id, res.message)
                    continue
                return

        if isinstance(state, types.AuthorizationStateWaitCode):
            bridge.state_name = "waitCode"
            code_info = state.code_info
            bridge.code_hint = describe_code_type(code_info.type)
            await _notify_status(workspace_id, "pending_auth", None, None)
            while True:
                user_input = (await bridge.next_code()).strip()
                if not user_input:
                    continue
                res = await c.checkAuthenticationCode(code=user_input)
                if isinstance(res, types.Error):
                    log.warning("Code step workspace=%s: %s", workspace_id, res.message)
                    continue
                return

        if isinstance(state, types.AuthorizationStateWaitOtherDeviceConfirmation):
            bridge.state_name = "waitQr"
            bridge.qr_link = state.link
            await _notify_status(workspace_id, "pending_auth", None, None)
            return

        if isinstance(state, types.AuthorizationStateWaitPassword):
            bridge.state_name = "waitPassword"
            bridge.password_hint = state.password_hint or ""
            await _notify_status(workspace_id, "pending_auth", None, None)
            while True:
                user_input = (await bridge.next_password()).strip()
                if not user_input:
                    continue
                res = await c.checkAuthenticationPassword(password=user_input)
                if isinstance(res, types.Error):
                    log.warning("Password step workspace=%s: %s", workspace_id, res.message)
                    continue
                return

        if isinstance(state, types.AuthorizationStateReady):
            bridge.state_name = "ready"
            bridge.qr_link = None
            bridge.last_tdlib_error = ""
            me = await c.getMe()
            me_id = str(me.id) if me and hasattr(me, "id") else None
            log.info("TDLib ready workspace=%s as id=%s", workspace_id, me_id)
            bridge.ready_event.set()
            await _notify_status(workspace_id, "connected", me_id, None)


@dataclass
class WorkspaceEntry:
    workspace_id: str
    client: Client
    bridge: AuthBridge
    folder_lock: FileLock
    files_dir: Path


_manager: ClientManager | None = None
_workspaces: dict[str, WorkspaceEntry] = {}
_runtime_lock = asyncio.Lock()


def _require_live_settings() -> None:
    s = get_settings()
    if not s.api_id or not s.api_hash:
        raise RuntimeError("TDLIB_MODE=live requires API_ID and API_HASH in .env")


async def ensure_client(workspace_id: str) -> WorkspaceEntry:
    _require_live_settings()
    settings = get_settings()
    async with _runtime_lock:
        if workspace_id in _workspaces:
            return _workspaces[workspace_id]

        base = Path(settings.tdlib_data_root).resolve() / workspace_id
        base.mkdir(parents=True, exist_ok=True)
        lock_path = base / ".tdlib-gateway.lock"
        folder_lock = FileLock(str(lock_path))
        if not folder_lock.acquire(timeout=0):
            raise RuntimeError(
                f"Another process holds the TDLib lock for this workspace: {lock_path}. "
                "Stop the other gateway instance or delete the stale lock if no process is running."
            )

        _install_set_tdlib_parameters_hook()
        loop = asyncio.get_running_loop()
        files_dir = str(base).replace("\\", "/")
        log_path = str((base / "tdlib.log").resolve()).replace("\\", "/")
        dek = (settings.database_encryption_key or "").strip() or "change_me_in_env"

        client = Client(
            api_id=settings.api_id,
            api_hash=settings.api_hash,
            files_directory=files_dir,
            database_encryption_key=dek,
            user_bot=True,
            loop=loop,
            td_verbosity=settings.td_verbosity,
            td_log=types.LogStreamFile(path=log_path, max_file_size=104_857_600),
        )
        bridge = AuthBridge()
        client._auth_bridge = bridge
        register_handlers(client, bridge, workspace_id)

        global _manager
        if _manager is None:
            _manager = ClientManager(
                [client],
                lib_path=client.lib_path,
                verbosity=client.td_verbosity,
                loop=loop,
            )
            await _manager.start()
        else:
            await _manager.add_client(client, start_client=True)

        entry = WorkspaceEntry(
            workspace_id=workspace_id,
            client=client,
            bridge=bridge,
            folder_lock=folder_lock,
            files_dir=base,
        )
        _workspaces[workspace_id] = entry
        log.info("TDLib client started for workspace %s -> %s", workspace_id, files_dir)
        return entry


async def wait_for_phone_prompt(workspace_id: str, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        entry = _workspaces.get(workspace_id)
        if not entry:
            await asyncio.sleep(0.15)
            continue
        st = getattr(entry.client, "authorization_state", None)
        if st == "authorizationStateWaitPhoneNumber":
            return
        if st == "authorizationStateReady":
            return
        await asyncio.sleep(0.15)
    raise TimeoutError(
        f"TDLib did not reach waitPhoneNumber within {timeout}s for workspace {workspace_id!r}. "
        "Check tdlib.log in the workspace folder under TDLIB_DATA_ROOT."
    )


async def submit_phone(workspace_id: str, phone: str) -> None:
    entry = await ensure_client(workspace_id)
    await wait_for_phone_prompt(workspace_id)
    auth = getattr(entry.client, "authorization_state", None)
    if auth == "authorizationStateReady":
        return
    if auth != "authorizationStateWaitPhoneNumber":
        log.info(
            "submit_phone ignored for workspace=%s (state=%s) — phone already submitted or wrong step",
            workspace_id,
            auth,
        )
        return
    await entry.bridge.put_phone(phone.strip())


async def submit_code(workspace_id: str, code: str) -> None:
    entry = _workspaces.get(workspace_id)
    if not entry:
        raise RuntimeError("No session; call /api/accounts/start first")
    await entry.bridge.put_code(code.strip())


async def submit_password(workspace_id: str, password: str) -> None:
    entry = _workspaces.get(workspace_id)
    if not entry:
        raise RuntimeError("No session; call /api/accounts/start first")
    await entry.bridge.put_password(password)


async def request_qr_link(workspace_id: str) -> dict[str, Any]:
    entry = await ensure_client(workspace_id)
    await wait_for_phone_prompt(workspace_id)
    c = entry.client
    br = entry.bridge
    auth = getattr(c, "authorization_state", None)

    if auth == "authorizationStateReady":
        raise RuntimeError("Already logged in; QR login is not needed.")

    if auth == "authorizationStateWaitOtherDeviceConfirmation" and br.qr_link:
        link = br.qr_link
        tok = _token_from_tg_login_url(link)
        return {"token": tok, "qrData": link, "status": "pending_auth"}

    if auth != "authorizationStateWaitPhoneNumber":
        raise RuntimeError(
            f"Cannot start QR from auth state {auth!r}. In Socialize use **Stop session**, wait a few seconds, "
            "then **Create QR login** again (do not submit phone in the same flow)."
        )

    await br.put_phone("qr")

    deadline = time.monotonic() + 35.0
    while time.monotonic() < deadline:
        if br.qr_link:
            link = br.qr_link
            tok = _token_from_tg_login_url(link)
            return {"token": tok, "qrData": link, "status": "pending_auth"}
        await asyncio.sleep(0.15)

    raise TimeoutError(
        "TDLib did not return a QR link. Check API_ID/API_HASH, tdlib.log, and that libtdjson is loaded."
    )


async def workspace_live_status_async(workspace_id: str) -> dict[str, Any]:
    entry = _workspaces.get(workspace_id)
    if not entry:
        return {"status": "disconnected", "accountId": None, "phone": None}
    c = entry.client
    # pytdbot can transiently raise while authorization state is not initialized yet
    # (startup/race or after shutdown). Status endpoint must never 500 on this.
    try:
        auth = c.authorization_state
    except Exception:
        auth = None
    status = _map_authorization_to_status(auth)
    if status == "disconnected":
        # Bridge still carries a meaningful auth step while property is unstable.
        br_state = getattr(entry.bridge, "state_name", "") or ""
        if br_state in ("waitPhoneNumber", "waitCode", "waitQr", "waitPassword"):
            status = "pending_auth"
        elif br_state == "ready":
            status = "connected"
    account_id: str | None = None
    if status == "connected":
        try:
            me = await c.getMe()
            if me and not isinstance(me, types.Error):
                account_id = str(me.id)
        except Exception:
            pass
    return {"status": status, "accountId": account_id, "phone": None}


async def qr_status(workspace_id: str, token: str) -> dict[str, Any]:
    _ = token
    st = await workspace_live_status_async(workspace_id)
    return {"status": st["status"]}


async def _stop_entry(entry: WorkspaceEntry) -> None:
    global _manager
    c = entry.client
    try:
        if _manager is not None and getattr(c, "client_id", None) is not None:
            await _manager.delete_client(c.client_id, close_client=True)
        else:
            await c.stop()
    except Exception:
        log.exception("Error stopping TDLib client for %s", entry.workspace_id)
    try:
        entry.folder_lock.release()
    except OSError:
        pass


async def remove_client(workspace_id: str) -> None:
    global _manager
    async with _runtime_lock:
        entry = _workspaces.pop(workspace_id, None)
        if not entry:
            return
        await _stop_entry(entry)

        if not _workspaces and _manager is not None:
            try:
                await _manager.close(close_all_clients=True)
            except Exception:
                log.exception("ClientManager close")
            _manager = None


def get_client(workspace_id: str) -> Client | None:
    e = _workspaces.get(workspace_id)
    return e.client if e else None


async def shutdown_all() -> None:
    global _manager
    async with _runtime_lock:
        for wid in list(_workspaces.keys()):
            entry = _workspaces.pop(wid, None)
            if entry:
                await _stop_entry(entry)
        if _manager is not None:
            try:
                await _manager.close(close_all_clients=True)
            except Exception:
                log.exception("ClientManager close on shutdown")
            _manager = None
