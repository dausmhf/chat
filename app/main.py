import time
import uuid
from typing import Optional
from fastapi import Depends, FastAPI, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from app.config import settings
from app.bootstrap import bootstrap_app
from app.admin.admin_command_service import execute_bot_on, execute_mark_payment, execute_takeover
from app.channels.starsender_adapter import StarsenderAdapter
from app.channels.waba_adapter import WabaCloudAdapter
from app.conversation.orchestrator import process_incoming_message
from app.ingestion.event_handler import get_or_create_client_uuid
from app.storage.database import get_db
from sqlalchemy.orm import Session

app = FastAPI(
    title="Autonomous AI CS Engine for Travel Umroh",
    version="1.0.0",
    description="Production-ready WhatsApp AI CS and Management Engine"
)


def _adapter_for_channel(channel: str):
    if channel == "starsender":
        return StarsenderAdapter()
    if channel == "waba":
        return WabaCloudAdapter()
    raise ValueError(f"Unsupported channel: {channel}")

# Startup event logging
@app.on_event("startup")
async def startup_event():
    print(f"App is booting under env: {settings.app_env}")
    print(f"App URL configured: {settings.app_url}")
    try:
        bootstrap_app()
    except Exception as e:
        print(f"Bootstrap checks failed on startup: {str(e)}")
        import sys
        sys.exit(1)

@app.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "env": settings.app_env,
        "version": "1.0.0"
    }

@app.get("/channels/{channel}/health", status_code=status.HTTP_200_OK)
async def channel_health(channel: str):
    adapter = _adapter_for_channel(channel)
    return adapter.health_check().dict()

@app.get("/webhooks/waba", status_code=status.HTTP_200_OK)
async def verify_waba_webhook(request: Request):
    params = request.query_params
    verify_token = params.get("hub.verify_token")
    expected = __import__("os").getenv("WABA_VERIFY_TOKEN", "")
    if expected and verify_token == expected:
        return int(params.get("hub.challenge", "0"))
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": "Invalid verify token"})

@app.post("/webhooks/{channel}", status_code=status.HTTP_200_OK)
async def receive_webhook(channel: str, request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    headers = dict(request.headers)
    adapter = _adapter_for_channel(channel)
    if not adapter.validate_signature(payload, headers):
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Invalid webhook signature"})
    result = process_incoming_message(db, channel, payload, headers=headers, send_reply=True)
    return result

@app.post("/webhooks/{channel}/{client_code}", status_code=status.HTTP_200_OK)
async def receive_client_webhook(channel: str, client_code: str, request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    payload["client_id"] = client_code
    headers = dict(request.headers)
    adapter = _adapter_for_channel(channel)
    if not adapter.validate_signature(payload, headers):
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Invalid webhook signature"})
    result = process_incoming_message(db, channel, payload, headers=headers, send_reply=True)
    return result


class BotOnRequest(BaseModel):
    conversation_id: uuid.UUID
    reason: str = "Admin returned control to bot"
    admin_id: Optional[uuid.UUID] = None


class TakeoverRequest(BaseModel):
    conversation_id: uuid.UUID
    admin_id: Optional[uuid.UUID] = None


class MarkPaymentRequest(BaseModel):
    invoice_id: uuid.UUID
    status: str
    admin_id: Optional[uuid.UUID] = None
    note: Optional[str] = None


@app.post("/admin/{client_code}/bot-on", status_code=status.HTTP_200_OK)
async def admin_bot_on(client_code: str, body: BotOnRequest, db: Session = Depends(get_db)):
    client_uuid = get_or_create_client_uuid(db, client_code)
    success, message = execute_bot_on(db, client_uuid, body.conversation_id, body.reason, body.admin_id)
    return {"success": success, "message": message}


@app.post("/admin/{client_code}/takeover", status_code=status.HTTP_200_OK)
async def admin_takeover(client_code: str, body: TakeoverRequest, db: Session = Depends(get_db)):
    client_uuid = get_or_create_client_uuid(db, client_code)
    success, message = execute_takeover(db, client_uuid, body.conversation_id, body.admin_id)
    return {"success": success, "message": message}


@app.post("/admin/{client_code}/mark-payment", status_code=status.HTTP_200_OK)
async def admin_mark_payment(client_code: str, body: MarkPaymentRequest, db: Session = Depends(get_db)):
    client_uuid = get_or_create_client_uuid(db, client_code)
    success, message = execute_mark_payment(db, client_uuid, body.invoice_id, body.status, body.admin_id, body.note)
    return {"success": success, "message": message}

# General error handling
@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    # Log the exception (in production use structured logger)
    print(f"Unhandled exception occurred on path {request.url.path}: {str(exc)}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "An internal server error occurred."}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
