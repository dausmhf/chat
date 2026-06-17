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
from app.admin.tenant_service import is_tenant_active, list_tenants
from app.channels.factory import build_channel_adapter
from app.channels.webhook_security import headers_with_query_secret
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
    if not is_tenant_active(client_code):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Tenant '{client_code}' is inactive.")
    return client_config_manager.load_all_configs(client_code)


def _webhook_headers(request: Request) -> dict:
    return headers_with_query_secret(request.headers, request.query_params)

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
async def admin_tenant_index(db: Session = Depends(get_db)):
    tenants = list_tenants(db)
    links = "".join(
        f'<li data-code="{tenant["client_code"]}"><a href="/admin/{tenant["client_code"]}/dashboard">{tenant["brand_name"]}</a><span>{tenant["client_code"]}</span><b class="{tenant["status"]}">{tenant["status"]}</b></li>'
        for tenant in tenants
    )
    return HTMLResponse(f"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HalloTravel Super Admin</title>
  <style>
    :root {{
      --bg: #f6f7f9;
      --panel: #ffffff;
      --line: #d9dee7;
      --text: #17202a;
      --muted: #617082;
      --green: #177245;
      --red: #b3261e;
      --blue: #2458a7;
      --amber: #946200;
    }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Arial, Helvetica, sans-serif; background: var(--bg); color: var(--text); }}
    header {{ height: 58px; display: flex; align-items: center; justify-content: space-between; gap: 12px; padding: 0 18px; border-bottom: 1px solid var(--line); background: var(--panel); }}
    main {{ display: grid; grid-template-columns: 360px 1fr; min-height: calc(100vh - 58px); }}
    h1 {{ font-size: 18px; margin: 0; }}
    h2 {{ font-size: 15px; margin: 0 0 10px; }}
    a {{ color: var(--blue); font-weight: 700; text-decoration: none; }}
    input, select {{ height: 36px; border: 1px solid var(--line); border-radius: 6px; padding: 0 10px; background: #fff; min-width: 0; }}
    button {{ height: 36px; border: 1px solid var(--line); border-radius: 6px; background: #fff; color: var(--text); font-weight: 700; cursor: pointer; padding: 0 12px; }}
    button.primary {{ background: var(--green); border-color: var(--green); color: #fff; }}
    button.danger {{ background: var(--red); border-color: var(--red); color: #fff; }}
    button.blue {{ background: var(--blue); border-color: var(--blue); color: #fff; }}
    button:disabled {{ opacity: .5; cursor: not-allowed; }}
    label {{ display: grid; gap: 5px; color: var(--muted); font-size: 12px; font-weight: 700; }}
    pre {{ white-space: pre-wrap; color: var(--muted); margin: 0; }}
    .top-actions {{ display: flex; align-items: center; gap: 8px; min-width: 320px; }}
    .top-actions input {{ width: 220px; }}
    .sidebar {{ border-right: 1px solid var(--line); background: var(--panel); overflow: auto; }}
    .content {{ padding: 18px; overflow: auto; }}
    .tenant-list {{ list-style: none; padding: 0; margin: 0; }}
    .tenant-row {{ display: grid; gap: 6px; width: 100%; text-align: left; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; height: auto; padding: 13px 14px; font-weight: 400; }}
    .tenant-row.active-row {{ background: #eef4ff; }}
    .row {{ display: flex; align-items: center; justify-content: space-between; gap: 10px; }}
    .name {{ font-weight: 700; overflow-wrap: anywhere; }}
    .code {{ color: var(--muted); font-family: monospace; font-size: 12px; }}
    .badge {{ display: inline-flex; align-items: center; min-height: 22px; border: 1px solid var(--line); border-radius: 999px; padding: 0 8px; font-size: 12px; font-weight: 700; white-space: nowrap; }}
    .badge.active, b.active {{ color: var(--green); border-color: #9fd3b8; background: #eefaf3; }}
    .badge.inactive, b.inactive {{ color: var(--red); border-color: #ebb0ac; background: #fff1f0; }}
    b.active, b.inactive {{ border: 1px solid; border-radius: 999px; padding: 2px 8px; font-size: 12px; }}
    .panel {{ background: var(--panel); border: 1px solid var(--line); padding: 14px; margin-bottom: 14px; }}
    .grid {{ display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }}
    .actions {{ display: flex; gap: 8px; flex-wrap: wrap; margin-top: 12px; }}
    .static-list {{ padding: 12px 14px; border-bottom: 1px solid var(--line); }}
    .static-list ul {{ list-style: none; padding: 0; margin: 8px 0 0; display: grid; gap: 8px; }}
    .static-list li {{ display: flex; justify-content: space-between; gap: 8px; }}
    .empty {{ color: var(--muted); padding: 14px; }}
    @media (max-width: 860px) {{
      header {{ height: auto; padding: 12px; align-items: stretch; flex-direction: column; }}
      main {{ grid-template-columns: 1fr; }}
      .sidebar {{ border-right: 0; border-bottom: 1px solid var(--line); max-height: 42vh; }}
      .grid {{ grid-template-columns: 1fr; }}
      .top-actions {{ width: 100%; min-width: 0; }}
      .top-actions input {{ flex: 1; width: auto; }}
    }}
  </style>
</head>
<body>
<header>
  <h1>HalloTravel Super Admin</h1>
  <div class="top-actions">
    <input id="token" type="password" placeholder="Admin token">
    <button id="saveToken">Simpan</button>
    <button id="refresh" class="blue">Refresh</button>
  </div>
</header>
<main>
  <aside class="sidebar">
    <div class="static-list">
      <strong>Tenant</strong>
      <ul>{links or "<li>Belum ada tenant.</li>"}</ul>
    </div>
    <div id="tenantList" class="tenant-list"><div class="empty">Masukkan token untuk memuat kontrol.</div></div>
  </aside>
  <section class="content">
    <div class="panel">
      <h2>Edit Tenant</h2>
      <div class="grid">
        <label>Client Code<input id="editClientCode" disabled></label>
        <label>Status<select id="editStatus"><option value="active">active</option><option value="inactive">inactive</option></select></label>
        <label>Brand Name<input id="editBrandName"></label>
        <label>Bot Name<input id="editBotName"></label>
        <label>Admin Notification<input id="editAdminPhone"></label>
        <label>Starsender API Env<input id="editStarsenderEnv"></label>
        <label>Starsender Webhook Secret Env<input id="editWebhookSecretEnv"></label>
        <label>Gemini API Env<input id="editGeminiEnv"></label>
      </div>
      <div class="actions">
        <button id="saveTenant" class="primary" disabled>Simpan Edit</button>
        <button id="enableTenant" class="primary" disabled>Aktifkan</button>
        <button id="disableTenant" class="danger" disabled>Nonaktifkan</button>
        <a id="dashboardLink" href="#">Buka Dashboard Tenant</a>
      </div>
    </div>
    <div class="panel">
      <h2>Tambah Tenant</h2>
      <div class="grid">
        <label>Client Code<input id="clientCode" placeholder="travel_baru"></label>
        <label>Brand Name<input id="brandName" placeholder="Nama travel / brand"></label>
        <label>Bot Name<input id="botName" value="Admin AI"></label>
        <label>Admin Notification<input id="adminPhone" placeholder="628xxx"></label>
        <label>Starsender API Env<input id="starsenderEnv" value="STARSENDER_API_KEY"></label>
        <label>Gemini API Env<input id="geminiEnv" value="GEMINI_API_KEY"></label>
      </div>
      <div class="actions">
        <button id="createTenant" class="primary">Buat Tenant</button>
      </div>
    </div>
    <div class="panel"><pre id="result">Siap.</pre></div>
  </section>
</main>
<script>
  let token = sessionStorage.getItem("adminToken") || "";
  let tenants = [];
  let selectedCode = "";
  const result = document.getElementById("result");
  const tokenInput = document.getElementById("token");
  tokenInput.value = token;

  function esc(value) {{
    return String(value ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[ch]));
  }}
  function show(value) {{
    result.textContent = typeof value === "string" ? value : JSON.stringify(value, null, 2);
  }}
  async function request(path, options = {{}}) {{
    const headers = Object.assign({{}}, options.headers || {{}});
    if (token) headers["X-Admin-Token"] = token;
    const res = await fetch(path, Object.assign({{}}, options, {{headers}}));
    const text = await res.text();
    let body = {{}};
    try {{ body = text ? JSON.parse(text) : {{}}; }} catch {{ body = {{detail: text}}; }}
    if (!res.ok) throw new Error(body.detail || text || `${{res.status}}`);
    return body;
  }}
  function renderTenants() {{
    const list = document.getElementById("tenantList");
    if (!tenants.length) {{
      list.innerHTML = '<div class="empty">Belum ada tenant.</div>';
      return;
    }}
    list.innerHTML = tenants.map(t => `
      <button class="tenant-row ${{t.client_code === selectedCode ? "active-row" : ""}}" data-code="${{esc(t.client_code)}}">
        <div class="row"><span class="name">${{esc(t.brand_name)}}</span><span class="badge ${{esc(t.status)}}">${{esc(t.status)}}</span></div>
        <div class="code">${{esc(t.client_code)}} · ${{esc(t.active_channel)}} · channel=${{t.channel_enabled ? "on" : "off"}}</div>
      </button>
    `).join("");
    list.querySelectorAll(".tenant-row").forEach(btn => btn.addEventListener("click", () => selectTenant(btn.dataset.code)));
  }}
  function selectedTenant() {{
    return tenants.find(t => t.client_code === selectedCode) || null;
  }}
  function selectTenant(code) {{
    selectedCode = code;
    const t = selectedTenant();
    const disabled = !t;
    document.getElementById("saveTenant").disabled = disabled;
    document.getElementById("enableTenant").disabled = disabled || t.status === "active";
    document.getElementById("disableTenant").disabled = disabled || t.status === "inactive";
    if (!t) return;
    document.getElementById("editClientCode").value = t.client_code;
    document.getElementById("editStatus").value = t.status;
    document.getElementById("editBrandName").value = t.brand_name || "";
    document.getElementById("editBotName").value = t.bot_name || "";
    document.getElementById("editAdminPhone").value = t.admin_notification_phone || "";
    document.getElementById("editStarsenderEnv").value = t.starsender_api_key_env || "";
    document.getElementById("editWebhookSecretEnv").value = t.starsender_webhook_secret_env || "";
    document.getElementById("editGeminiEnv").value = t.gemini_api_key_env || "";
    document.getElementById("dashboardLink").href = t.dashboard_url;
    renderTenants();
  }}
  async function loadTenants() {{
    const data = await request("/admin/api/tenants");
    tenants = data.tenants || [];
    if (!selectedCode && tenants[0]) selectedCode = tenants[0].client_code;
    renderTenants();
    selectTenant(selectedCode);
    show("Tenant dimuat.");
  }}
  async function createTenant() {{
    const payload = {{
      client_code: document.getElementById("clientCode").value.trim(),
      brand_name: document.getElementById("brandName").value.trim(),
      bot_name: document.getElementById("botName").value.trim() || "Admin AI",
      admin_phone: document.getElementById("adminPhone").value.trim(),
      starsender_api_key_env: document.getElementById("starsenderEnv").value.trim() || "STARSENDER_API_KEY",
      gemini_api_key_env: document.getElementById("geminiEnv").value.trim() || "GEMINI_API_KEY",
    }};
    const data = await request("/admin/api/tenants", {{
      method: "POST",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(payload),
    }});
    selectedCode = data.client_code;
    await loadTenants();
    show(data);
  }}
  async function saveTenant() {{
    if (!selectedCode) return;
    const payload = {{
      status: document.getElementById("editStatus").value,
      brand_name: document.getElementById("editBrandName").value.trim(),
      bot_name: document.getElementById("editBotName").value.trim(),
      admin_notification_phone: document.getElementById("editAdminPhone").value.trim(),
      starsender_api_key_env: document.getElementById("editStarsenderEnv").value.trim(),
      starsender_webhook_secret_env: document.getElementById("editWebhookSecretEnv").value.trim(),
      gemini_api_key_env: document.getElementById("editGeminiEnv").value.trim(),
    }};
    const data = await request(`/admin/api/tenants/${{selectedCode}}`, {{
      method: "PATCH",
      headers: {{"Content-Type": "application/json"}},
      body: JSON.stringify(payload),
    }});
    await loadTenants();
    show(data);
  }}
  async function setStatus(action) {{
    if (!selectedCode) return;
    const data = await request(`/admin/api/tenants/${{selectedCode}}/${{action}}`, {{method: "POST"}});
    await loadTenants();
    show(data);
  }}
  function run(fn) {{
    fn().catch(err => show(err.message));
  }}
  document.getElementById("saveToken").addEventListener("click", () => {{
    token = tokenInput.value.trim();
    sessionStorage.setItem("adminToken", token);
    run(loadTenants);
  }});
  document.getElementById("refresh").addEventListener("click", () => run(loadTenants));
  document.getElementById("createTenant").addEventListener("click", () => run(createTenant));
  document.getElementById("saveTenant").addEventListener("click", () => run(saveTenant));
  document.getElementById("enableTenant").addEventListener("click", () => run(() => setStatus("enable")));
  document.getElementById("disableTenant").addEventListener("click", () => run(() => setStatus("disable")));
  if (token) run(loadTenants);
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
    headers = _webhook_headers(request)
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
    headers = _webhook_headers(request)
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
