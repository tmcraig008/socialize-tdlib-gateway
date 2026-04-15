"""
Wire real TDLib (pytdbot) here when TDLIB_MODE=live.

This module is intentionally minimal: copy your working ClientManager / send logic
from your standalone Python project into these functions.
"""

from __future__ import annotations


async def send_message_live(
    workspace_id: str,
    chat_id: int,
    text: str,
    reply_to_message_id: int | None,
) -> int:
    raise NotImplementedError(
        "TDLIB_MODE=live but send_message_live() is not implemented. "
        "See IMPLEMENTATION.md and app/services/tdlib_live.py."
    )


async def send_media_live(
    workspace_id: str,
    chat_id: int,
    path: str,
    kind: str,
    caption: str | None,
) -> int:
    raise NotImplementedError(
        "TDLIB_MODE=live but send_media_live() is not implemented. "
        "See IMPLEMENTATION.md."
    )


async def on_incoming_update_for_workspace(workspace_id: str, update: object) -> None:
    """
    Call socialize_webhook.notify_incoming_message(...) when you parse a TDLib update.
    `update` is intentionally untyped until you wire pytdbot events.
    """
    _ = workspace_id, update
