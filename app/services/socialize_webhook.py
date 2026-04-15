import logging
from typing import Any
from urllib.parse import urlparse

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


def _normalized_socialize_base_url(raw: str) -> str:
    base = (raw or "").strip()
    if not base:
        raise RuntimeError(
            "SOCIALIZE_BACKEND_URL is empty. Set it to your Socialize API origin, "
            "for example: http://127.0.0.1:3002 or https://your-api.up.railway.app"
        )
    if not base.startswith(("http://", "https://")):
        # Railway domains should default to https; localhost/private dev hosts to http.
        host_hint = base.lower()
        if host_hint.startswith(("localhost", "127.", "0.0.0.0", "10.", "192.168.", "172.")):
            base = f"http://{base}"
        else:
            base = f"https://{base}"
    p = urlparse(base)
    if p.scheme not in {"http", "https"} or not p.netloc:
        raise RuntimeError(
            f"Invalid SOCIALIZE_BACKEND_URL={raw!r}. Include protocol, e.g. "
            "http://127.0.0.1:3002 or https://your-api.up.railway.app"
        )
    return base.rstrip("/")


async def post_to_socialize(payload: dict[str, Any]) -> None:
    settings = get_settings()
    base = _normalized_socialize_base_url(settings.socialize_backend_url)
    url = f"{base}/api/telegram/tdlib/webhook"
    headers: dict[str, str] = {"Content-Type": "application/json"}
    key = (settings.web_api_key or "").strip()
    if key:
        headers["x-api-key"] = key
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.post(url, json=payload, headers=headers)
            if r.status_code >= 400:
                logger.warning("Socialize webhook failed %s: %s", r.status_code, r.text[:500])
    except Exception as e:
        logger.exception("Socialize webhook error: %s", e)


async def notify_account_status(
    workspace_id: str,
    status: str,
    account_id: str | None = None,
    phone: str | None = None,
) -> None:
    await post_to_socialize(
        {
            "workspaceId": workspace_id,
            "updateType": "account_status",
            "status": status,
            "accountId": account_id,
            "phone": phone,
        }
    )


async def notify_incoming_message(workspace_id: str, message: dict[str, Any]) -> None:
    await post_to_socialize({"workspaceId": workspace_id, "message": message})
