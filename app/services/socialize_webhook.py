import logging
from typing import Any

import httpx

from app.config import get_settings

logger = logging.getLogger(__name__)


async def post_to_socialize(payload: dict[str, Any]) -> None:
    settings = get_settings()
    base = settings.socialize_backend_url.rstrip("/")
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
