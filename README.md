# Socialize TDLib Gateway

FastAPI service that bridges **Socialize** (Node) to **Telegram user accounts** via TDLib (pytdbot).

Socialize calls this service using `TDLIB_GATEWAY_URL` and optional `WEB_API_KEY`.

## Quick start (local)

```bash
cd socialize-tdlib-gateway
python -m venv .venv
.venv\Scripts\activate   # Windows
pip install -r requirements.txt
copy .env.example .env
# Edit .env: WEB_API_KEY, SOCIALIZE_BACKEND_URL, TDLIB_MODE=mock
uvicorn app.main:app --reload --port 8000
```

Open http://127.0.0.1:8000/health

## Environment variables

| Variable | Purpose |
|----------|---------|
| `WEB_API_KEY` | Shared secret; Socialize sends `x-api-key`; gateway verifies inbound requests |
| `SOCIALIZE_BACKEND_URL` | Base URL of Socialize API (e.g. `https://your-api.up.railway.app`) |
| `API_ID` / `API_HASH` | Telegram app credentials (required for `TDLIB_MODE=live`) |
| `TDLIB_DATA_ROOT` | Per-workspace TDLib files (use a volume in production) |
| `TDLIB_MODE` | `mock` (default) or `live` |

Match `WEB_API_KEY` with the Socialize backend variable of the same name.

## Railway

1. New service from this repo.
2. Set variables above; mount a volume on `TDLIB_DATA_ROOT` for live mode.
3. Start command (if not auto-detected): `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
4. Copy the public URL → Socialize `TDLIB_GATEWAY_URL` (no trailing slash).

## API contract

Endpoints mirror `Socialize/backend/src/services/tdlibGateway.ts`:

- `POST /api/accounts/start`
- `POST /api/accounts/stop`
- `GET /api/accounts/status?workspaceId=...`
- `POST /api/accounts/auth/phone|code|password`
- `POST /api/accounts/auth/qr`
- `GET /api/accounts/auth/qr-status?workspaceId=...&token=...`
- `POST /api/messages/send`
- `POST /api/media/send-photo|send-video|send-document|send-audio`

## Real Telegram

See [IMPLEMENTATION.md](./IMPLEMENTATION.md).
