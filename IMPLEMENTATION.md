# Implementing real TDLib (`TDLIB_MODE=live`)

## What works in `mock` mode

- All HTTP routes Socialize expects return JSON.
- Auth flow transitions (`pending_auth` → `connected` in mock) and account status webhooks fire.
- **Outgoing** sends return a fake `telegramMessageId` (no Telegram delivery).
- **Incoming** fan messages require real TDLib; mock does not synthesize them.

## What you must add for production

1. **Native TDLib** on the host (or use a Docker image that includes `libtdjson`).
2. Set Railway **persistent volume** mounted at `TDLIB_DATA_ROOT` (e.g. `/data/tdlib`).
3. Set env:
   - `API_ID`, `API_HASH` from https://my.telegram.org
   - `TDLIB_MODE=live`
   - `SOCIALIZE_BACKEND_URL` = your Socialize API URL
   - `WEB_API_KEY` = same value as Socialize backend

4. Implement in `app/services/tdlib_live.py`:
   - `send_message_live`
   - `send_media_live`
   - A long-running listener that parses TDLib updates and calls:
     `await socialize_webhook.notify_incoming_message(workspace_id, {...})`

Payload shape for incoming messages must match Socialize (`backend/src/routes/telegram.ts`):

```json
{
  "workspaceId": "<uuid>",
  "message": {
    "chatId": 123456789,
    "messageId": 42,
    "text": "optional",
    "caption": "optional",
    "sender": {
      "id": 123456789,
      "username": "optional",
      "firstName": "optional",
      "lastName": "optional"
    },
    "mediaType": "photo | video | document | audio | null",
    "mediaUrl": "/uploads/... or https://..."
  }
}
```

5. Port your **ClientManager** from the standalone Python project so each `workspaceId` maps to one TDLib client + one data directory (use `filelock` per folder as you already do).

## Suggested order

1. Deploy gateway in `mock` mode; confirm Socialize can call `/health` and `/api/accounts/status`.
2. Flip `TDLIB_MODE=live` only after `tdlib_live.py` sends a real test DM.
3. Add inbound update → webhook last (most moving parts).
