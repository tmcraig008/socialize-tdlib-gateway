import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import get_settings
from app.deps import verify_gateway_key
from app.services import session_store

load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


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


@app.post("/api/messages/send", dependencies=[Depends(verify_gateway_key)])
async def messages_send(body: SendMessageBody):
    return await session_store.send_text(
        body.workspaceId,
        body.chatId,
        body.text,
        body.replyToMessageId,
    )


class SendPhotoBody(BaseModel):
    workspaceId: str
    chatId: int
    photo: str
    caption: str | None = None


@app.post("/api/media/send-photo", dependencies=[Depends(verify_gateway_key)])
async def media_photo(body: SendPhotoBody):
    return await session_store.send_media(
        body.workspaceId, body.chatId, body.photo, "photo", body.caption
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
