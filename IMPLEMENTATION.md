# Implementing real TDLib (`TDLIB_MODE=live`)

## What works in `mock` mode

- All HTTP routes Socialize expects return JSON.
- Auth flow transitions (`pending_auth` → `connected` in mock) and account status webhooks fire.
- **Outgoing** sends return a fake `telegramMessageId` (no Telegram delivery).
- **Incoming** fan messages require real TDLib; mock does not synthesize them.

## Production (`TDLIB_MODE=live`)

1. **Native TDLib** / `libtdjson` on the host (or a Docker image that includes it).
2. Persistent volume on `TDLIB_DATA_ROOT` (each Socialize workspace id gets a subfolder).
3. Env:
   - `API_ID`, `API_HASH` from https://my.telegram.org
   - `TDLIB_MODE=live`
   - `SOCIALIZE_BACKEND_URL` (Socialize API base, no trailing slash)
   - `WEB_API_KEY` = same as Socialize backend
   - `DATABASE_ENCRYPTION_KEY` = stable secret per data root (same idea as “TG no Bot”)

Implemented:

- **Auth**: phone, code, password, QR via pytdbot (same flow as `TG no Bot`: literal `qr` → `requestQrCodeAuthentication`, QR link from `AuthorizationStateWaitOtherDeviceConfirmation`).
- **Sends**: `send_message_live` / `send_media_live` in `app/services/tdlib_live.py` (HTTP(S) media URLs are downloaded to a temp file, then sent as `inputFileLocal`).
- **Inbound**: `register_handlers` posts to Socialize `/api/telegram/tdlib/webhook` for non-outgoing messages (text + basic media metadata; `mediaUrl` may be null).

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

## Suggested order

1. Run gateway in `mock` mode; confirm Socialize can call `/health` and `/api/accounts/status`.
2. Set `TDLIB_MODE=live`, real `API_ID` / `API_HASH`, and sign in one workspace (phone or QR) using the Socialize Settings UI.
3. Send a test message from Socialize; confirm delivery in Telegram.
4. Reply from a fan in Telegram; confirm it appears in Socialize (webhook + inbound handler).
