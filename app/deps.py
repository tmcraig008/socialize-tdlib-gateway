from fastapi import Header, HTTPException

from app.config import get_settings


async def verify_gateway_key(x_api_key: str | None = Header(default=None)) -> None:
    settings = get_settings()
    expected = (settings.web_api_key or "").strip()
    if not expected:
        return
    if not x_api_key or x_api_key != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing x-api-key")
