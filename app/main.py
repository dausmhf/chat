import time
import uuid
from typing import Optional
from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel
from app.config import client_config_manager, settings
from app.bootstrap import bootstrap_app
from app.admin.admin_command_service import execute_bot_on, execute_mark_payment, execute_takeover
from app.admin.dashboard import require_admin_token, router as admin_dashboard_router
from app.channels.factory import build_channel_adapter
from app.conversation.orchestrator import process_incoming_message
from app.ingestion.event_handler import get_or_create_client_uuid
from app.storage.database import get_db
from sqlalchemy.orm import Session

app = FastAPI(
    title="Autonomous AI CS Engine for Travel Umroh",
    version="1.0.0",
    description="Production-ready WhatsApp AI CS and Management Engine"
)
app.include_router(admin_dashboard_router)


@app.middleware("http")
async def production_hardening(request: Request, call_next):
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 2_000_000:
        return JSONResponse(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, content={"detail": "Request too large."})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["Referrer-Policy"] = "same-origin"
    if request.url.path.startswith("/admin"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _adapter_for_channel(channel: str, client_code: str = "travel_alfalah"):
    configs = _load_tenant_configs_or_404(client_code)
    channel_settings = configs["channel"].get("channels", {}).get(channel, {})
    if not channel_settings or not channel_settings.get("enabled", False):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Channel '{channel}' is not enabled for tenant '{client_code}'.")
    return build_channel_adapter(channel, configs["channel"])


def _load_tenant_configs_or_404(client_code: str):
    if not client_config_manager.client_exists(client_code):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant '{client_code}' is not configured.")
    return client_config_manager.load_all_configs(client_code)

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

@app.get("/", status_code=status.HTTP_307_TEMPORARY_REDIRECT)
async def dashboard_redirect():
    return RedirectResponse(url="/admin")


@app.get("/admin", response_class=HTMLResponse)
async def admin_tenant_index():
    tenants = []
    for client_code in client_config_manager.list_client_ids():
        configs = client_config_manager.load_all_configs(client_code)
        tenants.append((client_code, configs["client"].get("brand_name", client_code)))
    links = "".join(
        f'<li><a href="/admin/{code}/dashboard">{brand}</a><span>{code}</span></li>'
        for code, brand in tenants
    )
    return HTMLResponse(f"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HalloTravel Tenants</title>
  <style>
    body {{ margin: 0; font-family: Arial, Helvetica, sans-serif; background: #f6f7f9; color: #17202a; }}
    main {{ max-width: 760px; margin: 0 auto; padding: 32px 18px; }}
    h1 {{ font-size: 22px; margin: 0 0 18px; }}
    section {{ margin-top: 22px; }}
    ul {{ list-style: none; padding: 0; margin: 0; border: 1px solid #d9dee7; background: #fff; }}
    li {{ display: flex; justify-content: space-between; gap: 12px; padding: 14px 16px; border-bottom: 1px solid #d9dee7; }}
    li:last-child {{ border-bottom: 0; }}
    a {{ color: #2458a7; font-weight: 700; text-decoration: none; }}
    span {{ color: #617082; font-family: monospace; }}
    .form {{ display: grid; gap: 8px; border: 1px solid #d9dee7; background: #fff; padding: 14px; }}
    input {{ height: 36px; border: 1px solid #d9dee7; border-radius: 6px; padding: 0 10px; }}
    button {{ height: 36px; border: 0; border-radius: 6px; background: #177245; color: #fff; font-weight: 700; cursor: pointer; }}
    pre {{ white-space: pre-wrap; color: #617082; }}
  </style>
</head>
<body>
<main>
  <h1>HalloTravel Admin</h1>
  <ul>{links or "<li>Belum ada tenant aktif.</li>"}</ul>
  <section>
    <h1>Tambah Tenant</h1>
    <div class="form">
      <input id="token" type="password" placeholder="Admin token">
      <input id="clientCode" placeholder="client_code, contoh: travel_baru">
      <input id="brandName" placeholder="Nama travel / brand">
      <input id="botName" placeholder="Nama bot" value="Admin AI">
      <input id="adminPhone" placeholder="Nomor admin, contoh: 628xxx">
      <input id="starsenderEnv" placeholder="Env Starsender" value="STARSENDER_API_KEY">
      <input id="geminiEnv" placeholder="Env Gemini" value="GEMINI_API_KEY">
      <button id="createTenant">Buat Tenant</button>
      <pre id="result"></pre>
    </div>
  </section>
</main>
<script>
  document.getElementById("createTenant").addEventListener("click", async () => {{
    const payload = {{
      client_code: document.getElementById("clientCode").value.trim(),
      brand_name: document.getElementById("brandName").value.trim(),
      bot_name: document.getElementById("botName").value.trim() || "Admin AI",
      admin_phone: document.getElementById("adminPhone").value.trim(),
      starsender_api_key_env: document.getElementById("starsenderEnv").value.trim() || "STARSENDER_API_KEY",
      gemini_api_key_env: document.getElementById("geminiEnv").value.trim() || "GEMINI_API_KEY",
    }};
    const token = document.getElementById("token").value.trim();
    const res = await fetch(`/admin/api/tenants?token=${{encodeURIComponent(token)}}`, {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(payload),
    }});
    document.getElementById("result").textContent = JSON.stringify(await res.json(), null, 2);
    if (res.ok) setTimeout(() => location.reload(), 800);
  }});
</script>
</body>
</html>""")

@app.get("/channels/{channel}/health", status_code=status.HTTP_200_OK)
async def channel_health(channel: str, client_code: str = "travel_alfalah"):
    adapter = _adapter_for_channel(channel, client_code)
    return adapter.health_check().dict()

@app.get("/webhooks/waba", status_code=status.HTTP_200_OK)
async def verify_waba_webhook(request: Request):
    params = request.query_params
    verify_token = params.get("hub.verify_token")
    client_code = params.get("client_code", "travel_alfalah")
    configs = _load_tenant_configs_or_404(client_code)
    expected = build_channel_adapter("waba", configs["channel"]).verify_token
    if expected and verify_token == expected:
        return int(params.get("hub.challenge", "0"))
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content={"detail": "Invalid verify token"})

@app.post("/webhooks/{channel}", status_code=status.HTTP_200_OK)
async def receive_webhook(channel: str, request: Request, db: Session = Depends(get_db)):
    payload = await request.json()
    client_code = payload.get("client_id", "travel_alfalah")
    headers = dict(request.headers)
    adapter = _adapter_for_channel(channel, client_code)
    if not adapter.validate_signature(payload, headers):
        return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content={"detail": "Invalid webhook signature"})
    result = process_incoming_message(db, channel, payload, headers=headers, send_reply=True)
    return result

@app.post("/webhooks/{channel}/{client_code}", status_code=status.HTTP_200_OK)
async def receive_client_webhook(channel: str, client_code: str, request: Request, db: Session = Depends(get_db)):
    _load_tenant_configs_or_404(client_code)
    payload = await request.json()
    payload["client_id"] = client_code
    headers = dict(request.headers)
    adapter = _adapter_for_channel(channel, client_code)
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
async def admin_bot_on(
    client_code: str,
    body: BotOnRequest,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    client_uuid = get_or_create_client_uuid(db, client_code)
    success, message = execute_bot_on(db, client_uuid, body.conversation_id, body.reason, body.admin_id)
    return {"success": success, "message": message}


@app.post("/admin/{client_code}/takeover", status_code=status.HTTP_200_OK)
async def admin_takeover(
    client_code: str,
    body: TakeoverRequest,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    client_uuid = get_or_create_client_uuid(db, client_code)
    success, message = execute_takeover(db, client_uuid, body.conversation_id, body.admin_id)
    return {"success": success, "message": message}


@app.post("/admin/{client_code}/mark-payment", status_code=status.HTTP_200_OK)
async def admin_mark_payment(
    client_code: str,
    body: MarkPaymentRequest,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
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
