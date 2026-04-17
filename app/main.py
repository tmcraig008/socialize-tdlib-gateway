import logging
import mimetypes
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.deps import verify_gateway_key
from app.services import session_store

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _largest_profile_photo_file_id(result: object) -> int | None:
    """Resolve TDLib getUserProfilePhotos result to a downloadable file id (largest JPEG size)."""
    if result is None:
        return None
    get_type_fn = getattr(result, "getType", None)
    if callable(get_type_fn) and get_type_fn() == "error":
        return None
    photos = getattr(result, "photos", None) or []
    if not photos:
        return None
    first = photos[0]
    sizes = getattr(first, "sizes", None) or []
    if not sizes:
        return None

    def size_key(s: object) -> int:
        w = int(getattr(s, "width", 0) or 0)
        h = int(getattr(s, "height", 0) or 0)
        return w * h

    best = max(sizes, key=size_key)
    photo = getattr(best, "photo", None)
    if photo is None:
        return None
    fid = int(getattr(photo, "id", 0) or 0)
    return fid if fid > 0 else None


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    logger.info("TDLib gateway starting (mode=%s)", settings.tdlib_mode)
    yield
    if settings.tdlib_mode != "mock":
        try:
            from app.services import tdlib_runtime

            await tdlib_runtime.shutdown_all()
        except Exception:
            logger.exception("TDLib shutdown")
    logger.info("TDLib gateway shutdown")


app = FastAPI(title="Socialize TDLib Gateway", lifespan=lifespan)


@app.exception_handler(RuntimeError)
async def runtime_error_handler(_: Request, exc: RuntimeError):
    return JSONResponse(status_code=400, content={"error": str(exc)})


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/media/tdlib-file")
async def tdlib_file(workspaceId: str, fileId: int):
    """
    Public media proxy for inbound TDLib attachments.
    The Socialize backend stores this URL as attachment_url for chat rendering.
    """
    settings = get_settings()
    if settings.tdlib_mode == "mock":
        return JSONResponse(status_code=404, content={"error": "TDLib live mode is required"})
    try:
        from app.services import tdlib_runtime
    except Exception:
        return JSONResponse(status_code=500, content={"error": "TDLib runtime unavailable"})

    entry = await tdlib_runtime.ensure_client(workspaceId)
    client = entry.client
    file_obj = await client.downloadFile(file_id=fileId, priority=32, synchronous=True)
    if file_obj is None or getattr(file_obj, "getType", lambda: "")() == "error":
        return JSONResponse(status_code=404, content={"error": "File not found"})

    local = getattr(file_obj, "local", None)
    local_path = getattr(local, "path", None) if local else None
    if not local_path or not os.path.exists(local_path):
        return JSONResponse(status_code=404, content={"error": "Downloaded file path missing"})

    media_type = mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    return FileResponse(path=local_path, media_type=media_type)


class ProfilePhotoFileBody(BaseModel):
    workspaceId: str
    telegramUserId: int = Field(..., ge=1)


@app.post("/api/users/profile-photo-file", dependencies=[Depends(verify_gateway_key)])
async def user_profile_photo_file(body: ProfilePhotoFileBody):
    """
    Returns TDLib remote file id for a user's current profile photo (largest size).
    The Socialize backend downloads bytes via GET /api/media/tdlib-file.
    """
    settings = get_settings()
    if settings.tdlib_mode == "mock":
        return JSONResponse(status_code=404, content={"error": "TDLib live mode is required"})
    try:
        from app.services import tdlib_runtime
    except Exception:
        return JSONResponse(status_code=500, content={"error": "TDLib runtime unavailable"})

    try:
        entry = await tdlib_runtime.ensure_client(body.workspaceId)
        result = await entry.client.getUserProfilePhotos(
            user_id=body.telegramUserId, offset=0, limit=1
        )
    except Exception as e:
        logger.exception("getUserProfilePhotos failed")
        return JSONResponse(status_code=400, content={"error": str(e)})

    file_id = _largest_profile_photo_file_id(result)
    if file_id is None:
        err = "No profile photo"
        if result is not None and getattr(result, "getType", lambda: "")() == "error":
            err = getattr(result, "message", None) or err
        return JSONResponse(status_code=404, content={"error": err})
    return {"fileId": file_id}


class StartBody(BaseModel):
    workspaceId: str
    phone: str | None = None


@app.post("/api/accounts/start", dependencies=[Depends(verify_gateway_key)])
async def accounts_start(body: StartBody):
    return await session_store.start_workspace(body.workspaceId, body.phone)


class StopBody(BaseModel):
    workspaceId: str


@app.post("/api/accounts/stop", dependencies=[Depends(verify_gateway_key)])
async def accounts_stop(body: StopBody):
    return await session_store.stop_workspace(body.workspaceId)


@app.get("/api/accounts/status", dependencies=[Depends(verify_gateway_key)])
async def accounts_status(workspaceId: str):
    return await session_store.workspace_status(workspaceId)


class PhoneBody(BaseModel):
    workspaceId: str
    phone: str


@app.post("/api/accounts/auth/phone", dependencies=[Depends(verify_gateway_key)])
async def auth_phone(body: PhoneBody):
    return await session_store.auth_phone(body.workspaceId, body.phone)


class CodeBody(BaseModel):
    workspaceId: str
    code: str


@app.post("/api/accounts/auth/code", dependencies=[Depends(verify_gateway_key)])
async def auth_code(body: CodeBody):
    return await session_store.auth_code(body.workspaceId, body.code)


class PasswordBody(BaseModel):
    workspaceId: str
    password: str


@app.post("/api/accounts/auth/password", dependencies=[Depends(verify_gateway_key)])
async def auth_password(body: PasswordBody):
    return await session_store.auth_password(body.workspaceId, body.password)


class QrBody(BaseModel):
    workspaceId: str


@app.post("/api/accounts/auth/qr", dependencies=[Depends(verify_gateway_key)])
async def auth_qr(body: QrBody):
    return await session_store.auth_qr_create(body.workspaceId)


@app.get("/api/accounts/auth/qr-status", dependencies=[Depends(verify_gateway_key)])
async def auth_qr_status(workspaceId: str, token: str):
    return await session_store.auth_qr_status(workspaceId, token)


class SendMessageBody(BaseModel):
    workspaceId: str
    chatId: int
    text: str
    replyToMessageId: int | None = None
    linkLabel: str | None = None
    linkUrl: str | None = None


@app.post("/api/messages/send", dependencies=[Depends(verify_gateway_key)])
async def messages_send(body: SendMessageBody):
    return await session_store.send_text(
        body.workspaceId,
        body.chatId,
        body.text,
        body.replyToMessageId,
        link_label=body.linkLabel,
        link_url=body.linkUrl,
    )


class EditMessageBody(BaseModel):
    workspaceId: str
    chatId: int
    messageId: int
    text: str
    linkLabel: str | None = None
    linkUrl: str | None = None


@app.post("/api/messages/edit", dependencies=[Depends(verify_gateway_key)])
async def messages_edit(body: EditMessageBody):
    return await session_store.edit_text(
        body.workspaceId,
        body.chatId,
        body.messageId,
        body.text,
        link_label=body.linkLabel,
        link_url=body.linkUrl,
    )


class SendPhotoBody(BaseModel):
    workspaceId: str
    chatId: int
    photo: str
    caption: str | None = None
    linkLabel: str | None = None
    linkUrl: str | None = None


@app.post("/api/media/send-photo", dependencies=[Depends(verify_gateway_key)])
async def media_photo(body: SendPhotoBody):
    return await session_store.send_media(
        body.workspaceId,
        body.chatId,
        body.photo,
        "photo",
        body.caption,
        link_label=body.linkLabel,
        link_url=body.linkUrl,
    )


class SendVideoBody(BaseModel):
    workspaceId: str
    chatId: int
    video: str
    caption: str | None = None


@app.post("/api/media/send-video", dependencies=[Depends(verify_gateway_key)])
async def media_video(body: SendVideoBody):
    return await session_store.send_media(
        body.workspaceId, body.chatId, body.video, "video", body.caption
    )


class SendDocumentBody(BaseModel):
    workspaceId: str
    chatId: int
    document: str
    caption: str | None = None


@app.post("/api/media/send-document", dependencies=[Depends(verify_gateway_key)])
async def media_document(body: SendDocumentBody):
    return await session_store.send_media(
        body.workspaceId, body.chatId, body.document, "document", body.caption
    )


class SendAudioBody(BaseModel):
    workspaceId: str
    chatId: int
    audio: str
    caption: str | None = None


@app.post("/api/media/send-audio", dependencies=[Depends(verify_gateway_key)])
async def media_audio(body: SendAudioBody):
    return await session_store.send_media(
        body.workspaceId, body.chatId, body.audio, "audio", body.caption
    )
