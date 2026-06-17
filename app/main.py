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
from app.admin.tenant_service import is_tenant_active
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
    return HTMLResponse(_super_admin_html())


def _super_admin_html() -> str:
    return """<!doctype html>
<html lang="id">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HalloTravel · Super Admin</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
:root{
  --bg:#f8fafc;--white:#ffffff;--panel:#ffffff;
  --border:#e2e8f0;--border-light:#f1f5f9;
  --text:#0f172a;--text2:#475569;--muted:#94a3b8;
  --primary:#3b82f6;--primary-light:#60a5fa;--primary-bg:#eff6ff;--primary-border:#bfdbfe;
  --green:#10b981;--green-bg:#ecfdf5;--green-border:#a7f3d0;
  --red:#ef4444;--red-bg:#fef2f2;--red-border:#fecaca;
  --amber:#f59e0b;--amber-bg:#fffbeb;--amber-border:#fde68a;
  --purple:#8b5cf6;--purple-bg:#f5f3ff;
  --radius:12px;--radius-lg:16px;
  --shadow:0 1px 3px rgba(15,23,42,.05),0 1px 2px rgba(15,23,42,.03);
  --shadow-md:0 4px 6px -1px rgba(15,23,42,.08),0 2px 4px -2px rgba(15,23,42,.08);
  --shadow-lg:0 10px 15px -3px rgba(15,23,42,.08),0 4px 6px -4px rgba(15,23,42,.08);
  --transition:all .2s cubic-bezier(0.4,0,0.2,1);
}
html{font-size:14px}
body{font-family:'Inter',system-ui,-apple-system,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow:hidden}

/* Toast */
#toast{position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px}
.toast-item{padding:12px 20px;border-radius:var(--radius);font-size:13px;font-weight:600;color:#fff;box-shadow:var(--shadow-lg);animation:toastIn .3s ease,toastOut .4s ease 2.6s forwards;max-width:380px}
.toast-item.success{background:var(--green)}
.toast-item.error{background:var(--red)}
.toast-item.info{background:var(--primary)}
@keyframes toastIn{from{opacity:0;transform:translateY(-12px) scale(.95)}to{opacity:1;transform:translateY(0) scale(1)}}
@keyframes toastOut{from{opacity:1}to{opacity:0;transform:translateY(-8px)}}

/* Layout */
.app{display:grid;grid-template-columns:268px 1fr;height:100vh}
.svg-icon{width:18px;height:18px;display:inline-block;vertical-align:middle;fill:none;stroke:currentColor;stroke-width:2;stroke-linecap:round;stroke-linejoin:round;flex-shrink:0}
.icon-title{display:flex;align-items:center;gap:8px}
.icon-title .svg-icon{width:19px;height:19px}

/* Sidebar */
.sidebar{background:var(--white);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.sidebar-brand{height:72px;padding:0 18px;display:flex;align-items:center;gap:12px;border-bottom:1px solid var(--border)}
.sidebar-brand .logo-icon{width:38px;height:38px;background:var(--primary);border-radius:12px;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:16px;box-shadow:0 8px 18px rgba(37,99,235,.22)}
.sidebar-brand h1{font-size:16px;font-weight:800;color:var(--text);letter-spacing:-.3px}

.sidebar-search{padding:18px 14px 10px;position:relative}
.sidebar-search input{width:100%;height:42px;border:1px solid var(--border);border-radius:12px;padding:0 14px 0 40px;font-size:13px;background:#f8fafc;color:var(--text);outline:none;transition:var(--transition)}
.sidebar-search input:focus{border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-bg)}
.sidebar-search .search-icon{position:absolute;left:27px;top:31px;width:16px;height:16px;color:var(--muted);pointer-events:none}

.sidebar-nav{padding:8px 12px;display:flex;flex-direction:column;gap:4px}
.nav-label{font-size:10px;font-weight:700;color:var(--muted);text-transform:uppercase;letter-spacing:1.2px;padding:12px 8px 6px}
.nav-item{display:flex;align-items:center;gap:11px;padding:11px 12px;border-radius:12px;font-size:13px;font-weight:600;color:var(--text2);cursor:pointer;transition:var(--transition);border:none;background:transparent;width:100%;text-align:left}
.nav-item:hover{background:var(--bg);color:var(--text)}
.nav-item.active{background:var(--primary);color:#fff;font-weight:600;box-shadow:0 2px 8px rgba(37,99,235,.3)}
.nav-item .icon{width:22px;height:22px;display:flex;align-items:center;justify-content:center}
.nav-item .badge-count{margin-left:auto;background:var(--red);color:#fff;font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px;min-width:20px;text-align:center}

.sidebar-tenants{flex:1;overflow-y:auto;padding:0 14px 12px}
.tenant-item{width:100%;min-width:0;text-align:left;border:1px solid transparent;border-radius:14px;padding:10px 11px;display:grid;gap:3px;cursor:pointer;transition:var(--transition);background:transparent;margin-bottom:4px}
.tenant-item:hover{background:var(--bg)}
.tenant-item.selected{background:var(--primary-bg);border-color:var(--primary-border)}
.tenant-item .t-row{display:grid;grid-template-columns:minmax(0,1fr) auto;align-items:center;gap:8px;width:100%;min-width:0}
.tenant-item .t-name{font-weight:700;font-size:12.5px;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0;line-height:1.25}
.tenant-item .badge{flex-shrink:0;font-size:10px;padding:3px 8px;max-width:92px}
.tenant-item .t-code{display:block;font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:100%;line-height:1.25}

.sidebar-footer{padding:12px 16px;border-top:1px solid var(--border)}
.sidebar-footer button{width:100%}

/* Badges */
.badge{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border-radius:20px;font-size:11px;font-weight:600}
.badge::before{content:"";width:6px;height:6px;border-radius:50%}
.badge.active{color:var(--green);background:var(--green-bg);border:1px solid var(--green-border)}
.badge.active::before{background:var(--green)}
.badge.inactive{color:var(--red);background:var(--red-bg);border:1px solid var(--red-border)}
.badge.inactive::before{background:var(--red)}

/* Main */
.main-area{display:flex;flex-direction:column;overflow:hidden}

/* Top bar */
.topbar{height:72px;background:var(--white);border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;padding:0 34px;flex-shrink:0}
.topbar-left{display:flex;align-items:center;gap:16px}
.topbar-left h2{font-size:18px;font-weight:700}
.topbar-right{display:flex;align-items:center;gap:12px}
.topbar-right input{height:42px;width:240px;border:1px solid var(--border);border-radius:12px;padding:0 14px;font-size:13px;background:#f8fafc;outline:none}
.topbar-right input:focus{border-color:var(--primary)}
.user-avatar{width:36px;height:36px;border-radius:50%;background:linear-gradient(135deg,var(--primary),var(--purple));display:flex;align-items:center;justify-content:center;color:#fff;font-weight:700;font-size:14px;cursor:pointer}

/* Content */
.content{flex:1;overflow-y:auto;padding:28px 34px}

/* Inputs */
input,select,textarea{font-family:inherit;font-size:13px;color:var(--text);background:var(--white);border:1px solid var(--border);border-radius:12px;padding:0 14px;height:42px;outline:none;transition:var(--transition)}
input:focus,select:focus,textarea:focus{border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-bg)}
textarea{height:auto;min-height:80px;padding:12px 14px;resize:vertical}
input::placeholder{color:var(--muted)}
select{cursor:pointer;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' fill='%238b92a8' viewBox='0 0 16 16'%3E%3Cpath d='M8 11L3 6h10z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 12px center;padding-right:32px}

/* Buttons */
button{font-family:inherit;font-size:13px;font-weight:700;border:1px solid var(--border);border-radius:12px;background:var(--white);color:var(--text);height:42px;padding:0 20px;cursor:pointer;transition:var(--transition);white-space:nowrap;display:inline-flex;align-items:center;justify-content:center;gap:7px}
button:hover{background:var(--bg);border-color:var(--border)}
button:active{transform:scale(.98)}
button:disabled{opacity:.4;cursor:not-allowed;transform:none}
button.primary{background:var(--primary);border-color:var(--primary);color:#fff;box-shadow:0 2px 6px rgba(37,99,235,.25)}
button.primary:hover{background:var(--primary-light)}
button.danger{background:var(--white);border-color:var(--red-border);color:var(--red)}
button.danger:hover{background:var(--red-bg)}
button.success{background:var(--white);border-color:var(--green-border);color:var(--green)}
button.success:hover{background:var(--green-bg)}
button.ghost{background:transparent;border-color:transparent;color:var(--text2)}
button.ghost:hover{background:var(--bg);color:var(--text)}
button.sm{height:36px;font-size:12px;padding:0 14px;border-radius:10px}

/* Stat Cards */
.stats-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:14px;margin-bottom:24px}
.stat-card{background:var(--white);border:1px solid var(--border);border-radius:16px;padding:18px 20px;display:flex;align-items:center;gap:16px;transition:var(--transition);box-shadow:var(--shadow)}
.stat-card:hover{box-shadow:var(--shadow-md);transform:translateY(-2px)}
.stat-icon{width:48px;height:48px;border-radius:14px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.stat-icon .svg-icon{width:22px;height:22px}
.stat-icon.blue{background:var(--primary-bg);color:var(--primary)}
.stat-icon.green{background:var(--green-bg);color:var(--green)}
.stat-icon.purple{background:var(--purple-bg);color:var(--purple)}
.stat-icon.amber{background:var(--amber-bg);color:var(--amber)}
.stat-info .stat-value{font-size:24px;font-weight:800;color:var(--text);line-height:1}
.stat-info .stat-label{font-size:12px;color:var(--muted);font-weight:500;margin-top:4px}

/* Cards */
.card{background:var(--white);border:1px solid var(--border);border-radius:16px;padding:24px 28px;margin-bottom:20px;box-shadow:var(--shadow)}
.card-header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:20px}
.card-header h3{font-size:16px;font-weight:700;color:var(--text)}
.card-header .subtitle{font-size:12px;color:var(--muted);font-weight:500}

/* Tabs */
.tabs{display:flex;gap:4px;margin-bottom:24px;overflow-x:auto;background:var(--white);border:1px solid var(--border);border-radius:14px;padding:5px;box-shadow:var(--shadow)}
.tab-btn{border:none;border-radius:8px;background:transparent;color:var(--text2);font-weight:500;padding:10px 18px;height:auto;font-size:13px;cursor:pointer;transition:var(--transition);white-space:nowrap;box-shadow:none}
.tab-btn:hover{color:var(--text);background:var(--bg)}
.tab-btn.active{color:#fff;background:var(--primary);font-weight:600;box-shadow:0 2px 6px rgba(37,99,235,.25)}
.tab-panel{display:none}
.tab-panel.active{display:block;animation:fadeIn .25s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:translateY(0)}}

/* Form */
.form-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:16px}
.form-group{display:flex;flex-direction:column;gap:6px}
.form-group.full{grid-column:1/-1}
.form-label{font-size:12px;font-weight:600;color:var(--text2)}
.form-actions{display:flex;gap:8px;margin-top:20px;flex-wrap:wrap}

/* Key Row */
.key-row{display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center;padding:14px 0;border-bottom:1px solid var(--border-light)}
.key-row:last-child{border-bottom:none}
.key-info{display:flex;flex-direction:column;gap:3px}
.key-name{font-weight:600;font-size:13px;color:var(--text)}
.key-source{font-size:11px;font-weight:500}
.key-source.set{color:var(--green)}
.key-source.not-set{color:var(--red)}
.key-input-wrap{display:flex;gap:6px;align-items:center}
.key-input-wrap input{width:260px}

/* Toggle */
.toggle-row{display:flex;align-items:center;justify-content:space-between;padding:12px 0;border-bottom:1px solid var(--border-light)}
.toggle-row:last-child{border-bottom:none}
.toggle-label{font-weight:600;font-size:13px}
.toggle{position:relative;width:46px;height:26px;background:var(--border);border-radius:13px;cursor:pointer;transition:var(--transition);flex-shrink:0}
.toggle.on{background:var(--primary)}
.toggle::after{content:"";position:absolute;top:3px;left:3px;width:20px;height:20px;background:#fff;border-radius:50%;transition:var(--transition);box-shadow:0 1px 3px rgba(0,0,0,.15)}
.toggle.on::after{left:23px}

/* JSON Editor */
.json-editor{width:100%;min-height:300px;font-family:'Courier New',monospace;font-size:12px;line-height:1.6;background:var(--bg);color:var(--text);border:1px solid var(--border);border-radius:var(--radius);padding:16px;resize:vertical}

/* URL display */
.url-display{display:flex;align-items:center;gap:8px;padding:12px 16px;background:var(--bg);border:1px solid var(--border);border-radius:var(--radius);font-family:'Courier New',monospace;font-size:12px;color:var(--primary);word-break:break-all}

/* Empty state */
.empty-state{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:80px 20px;color:var(--muted);text-align:center}
.empty-state .icon{width:56px;height:56px;border-radius:18px;display:flex;align-items:center;justify-content:center;color:var(--muted);background:var(--bg);opacity:1}
.empty-state .icon .svg-icon{width:28px;height:28px;opacity:.7}
.empty-state p{font-size:15px;max-width:300px;line-height:1.5}

/* Modal */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.4);backdrop-filter:blur(4px);z-index:100;display:none;align-items:center;justify-content:center}
.modal-overlay.show{display:flex}
.modal{background:var(--white);border:1px solid var(--border);border-radius:var(--radius-lg);padding:32px;width:90%;max-width:520px;box-shadow:var(--shadow-lg);animation:modalIn .25s ease}
@keyframes modalIn{from{opacity:0;transform:scale(.95) translateY(8px)}to{opacity:1;transform:scale(1) translateY(0)}}
.modal h2{font-size:20px;font-weight:800;margin-bottom:6px}
.modal .modal-sub{font-size:13px;color:var(--muted);margin-bottom:24px}

/* Table */
table{width:100%;border-collapse:collapse;font-size:13px}
th{text-align:left;padding:10px 12px;font-weight:600;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px;border-bottom:2px solid var(--border)}
td{padding:10px 12px;border-bottom:1px solid var(--border-light);color:var(--text2)}
tr:hover td{background:var(--bg)}

/* Responsive */
@media(max-width:900px){
  .app{grid-template-columns:1fr}
  .sidebar{display:none}
  .form-grid{grid-template-columns:1fr}
  .stats-row{grid-template-columns:repeat(2,1fr)}
  .key-input-wrap input{width:160px}
}
</style>
</head>
<body>
<svg aria-hidden="true" width="0" height="0" style="position:absolute;overflow:hidden">
  <defs>
    <symbol id="i-search" viewBox="0 0 24 24"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></symbol>
    <symbol id="i-home" viewBox="0 0 24 24"><path d="m3 10.5 9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M9 20v-6h6v6"/></symbol>
    <symbol id="i-building" viewBox="0 0 24 24"><path d="M4 21h16"/><path d="M6 21V5a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v16"/><path d="M9 7h1M14 7h1M9 11h1M14 11h1M9 15h1M14 15h1"/></symbol>
    <symbol id="i-settings" viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.9-.3 1.7 1.7 0 0 0-1 1.6V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1-1.6 1.7 1.7 0 0 0-1.9.3l-.1.1A2 2 0 1 1 4.2 17l.1-.1a1.7 1.7 0 0 0 .3-1.9 1.7 1.7 0 0 0-1.6-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.6-1 1.7 1.7 0 0 0-.3-1.9l-.1-.1A2 2 0 1 1 7 4.2l.1.1a1.7 1.7 0 0 0 1.9.3h.1a1.7 1.7 0 0 0 1-1.6V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.6 1.7 1.7 0 0 0 1.9-.3l.1-.1A2 2 0 1 1 19.8 7l-.1.1a1.7 1.7 0 0 0-.3 1.9v.1a1.7 1.7 0 0 0 1.6 1h.1a2 2 0 1 1 0 4H21a1.7 1.7 0 0 0-1.6 1Z"/></symbol>
    <symbol id="i-lock" viewBox="0 0 24 24"><rect x="5" y="10" width="14" height="11" rx="2"/><path d="M8 10V7a4 4 0 0 1 8 0v3"/></symbol>
    <symbol id="i-clipboard" viewBox="0 0 24 24"><path d="M9 3h6l1 2h3v16H5V5h3l1-2Z"/><path d="M9 9h6M9 13h6M9 17h4"/></symbol>
    <symbol id="i-alert" viewBox="0 0 24 24"><path d="M12 3 2 21h20L12 3Z"/><path d="M12 9v5M12 18h.01"/></symbol>
    <symbol id="i-dashboard" viewBox="0 0 24 24"><rect x="3" y="3" width="7" height="8" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/><rect x="3" y="15" width="7" height="6" rx="1"/></symbol>
    <symbol id="i-trash" viewBox="0 0 24 24"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 15H6L5 6"/><path d="M10 11v6M14 11v6"/></symbol>
    <symbol id="i-message" viewBox="0 0 24 24"><path d="M21 12a8 8 0 0 1-8 8H6l-3 3v-6a8 8 0 1 1 18-5Z"/></symbol>
    <symbol id="i-users" viewBox="0 0 24 24"><path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M22 21v-2a4 4 0 0 0-3-3.9"/><path d="M16 3.1a4 4 0 0 1 0 7.8"/></symbol>
    <symbol id="i-mail" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="m3 7 9 6 9-6"/></symbol>
    <symbol id="i-box" viewBox="0 0 24 24"><path d="m21 8-9-5-9 5 9 5 9-5Z"/><path d="M3 8v8l9 5 9-5V8"/><path d="M12 13v8"/></symbol>
    <symbol id="i-book" viewBox="0 0 24 24"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"/><path d="M4 4.5A2.5 2.5 0 0 1 6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15Z"/></symbol>
    <symbol id="i-save" viewBox="0 0 24 24"><path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2Z"/><path d="M17 21v-8H7v8M7 3v5h8"/></symbol>
    <symbol id="i-pause" viewBox="0 0 24 24"><path d="M8 5v14M16 5v14"/></symbol>
    <symbol id="i-play" viewBox="0 0 24 24"><path d="m8 5 11 7-11 7V5Z"/></symbol>
    <symbol id="i-plus" viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></symbol>
    <symbol id="i-key" viewBox="0 0 24 24"><circle cx="7.5" cy="15.5" r="4.5"/><path d="m11 12 9-9M15 6l3 3M17 4l3 3"/></symbol>
    <symbol id="i-eye" viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/></symbol>
    <symbol id="i-radio" viewBox="0 0 24 24"><path d="M4.9 19.1a10 10 0 0 1 0-14.2M8.5 15.5a5 5 0 0 1 0-7M15.5 8.5a5 5 0 0 1 0 7M19.1 4.9a10 10 0 0 1 0 14.2"/><circle cx="12" cy="12" r="1"/></symbol>
    <symbol id="i-bot" viewBox="0 0 24 24"><rect x="4" y="8" width="16" height="12" rx="2"/><path d="M12 4v4M8 2h8M9 13h.01M15 13h.01M9 17h6"/></symbol>
    <symbol id="i-shield" viewBox="0 0 24 24"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"/></symbol>
    <symbol id="i-zap" viewBox="0 0 24 24"><path d="M13 2 3 14h8l-1 8 11-13h-8l1-7Z"/></symbol>
    <symbol id="i-credit-card" viewBox="0 0 24 24"><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 10h18M7 15h3"/></symbol>
    <symbol id="i-bank" viewBox="0 0 24 24"><path d="m3 10 9-7 9 7"/><path d="M5 10h14M6 10v9M10 10v9M14 10v9M18 10v9M4 19h16"/></symbol>
    <symbol id="i-flag" viewBox="0 0 24 24"><path d="M5 22V4"/><path d="M5 4h12l-1 5 1 5H5"/></symbol>
    <symbol id="i-code" viewBox="0 0 24 24"><path d="m8 9-4 3 4 3M16 9l4 3-4 3M14 4l-4 16"/></symbol>
    <symbol id="i-refresh" viewBox="0 0 24 24"><path d="M20 11a8.1 8.1 0 0 0-15.5-2M4 5v4h4M4 13a8.1 8.1 0 0 0 15.5 2M20 19v-4h-4"/></symbol>
  </defs>
</svg>
<div class="app">
<!-- Sidebar -->
<aside class="sidebar">
  <div class="sidebar-brand">
    <div class="logo-icon">H</div>
    <h1>HalloTravel</h1>
  </div>
  <div class="sidebar-search">
    <svg class="svg-icon search-icon"><use href="#i-search"></use></svg>
    <input id="searchTenant" type="text" placeholder="Cari tenant...">
  </div>
  <div class="sidebar-nav">
    <div class="nav-label">Menu</div>
    <button class="nav-item active" id="navHome"><span class="icon"><svg class="svg-icon"><use href="#i-home"></use></svg></span> Home</button>
    <button class="nav-item" id="navTenants"><span class="icon"><svg class="svg-icon"><use href="#i-building"></use></svg></span> Tenants</button>
    <button class="nav-item" id="navSettings"><span class="icon"><svg class="svg-icon"><use href="#i-settings"></use></svg></span> Settings</button>
  </div>
  <div class="sidebar-nav">
    <div class="nav-label">Tenants</div>
  </div>
  <div class="sidebar-tenants" id="tenantList">
    <div class="empty-state" style="padding:30px 10px"><div class="icon"><svg class="svg-icon"><use href="#i-lock"></use></svg></div><p style="font-size:12px">Masukkan token untuk memuat</p></div>
  </div>
  <div class="sidebar-footer">
    <button id="openAddModal" class="primary" style="width:100%"><svg class="svg-icon"><use href="#i-plus"></use></svg> Tambah Tenant</button>
  </div>
</aside>

<!-- Main -->
<div class="main-area">
  <div class="topbar">
    <div class="topbar-left"><h2 id="pageTitle">Dashboard</h2></div>
    <div class="topbar-right">
      <input id="token" type="password" placeholder="Admin token">
      <button id="saveToken" class="primary sm">Login</button>
      <button id="refresh" class="ghost sm" title="Refresh"><svg class="svg-icon"><use href="#i-refresh"></use></svg></button>
      <div class="user-avatar" title="Super Admin">SA</div>
    </div>
  </div>
  <div class="content" id="mainContent">
    <div class="empty-state"><div class="icon"><svg class="svg-icon"><use href="#i-clipboard"></use></svg></div><p>Pilih tenant dari sidebar atau masukkan admin token untuk memulai</p></div>
  </div>
</div>
</div>

<!-- Add Tenant Modal -->
<div class="modal-overlay" id="addModal">
<div class="modal">
  <h2>Tambah Tenant Baru</h2>
  <p class="modal-sub">Buat tenant baru dengan konfigurasi default</p>
  <div class="form-grid">
    <div class="form-group"><div class="form-label">Client Code</div><input id="newClientCode" placeholder="travel_baru"></div>
    <div class="form-group"><div class="form-label">Brand Name</div><input id="newBrandName" placeholder="Nama Travel"></div>
    <div class="form-group"><div class="form-label">Bot Name</div><input id="newBotName" value="Admin AI"></div>
    <div class="form-group"><div class="form-label">Admin Phone</div><input id="newAdminPhone" placeholder="628xxx"></div>
  </div>
  <div class="form-actions">
    <button id="createTenantBtn" class="primary"><svg class="svg-icon"><use href="#i-plus"></use></svg> Buat Tenant</button>
    <button id="closeAddModal" class="ghost">Batal</button>
  </div>
</div>
</div>

<!-- Delete Modal -->
<div class="modal-overlay" id="deleteModal">
<div class="modal">
  <h2 class="icon-title" style="color:var(--red)"><svg class="svg-icon"><use href="#i-alert"></use></svg> Hapus Tenant</h2>
  <p class="modal-sub">Tenant akan dinonaktifkan dan folder config akan di-rename. Data di database tetap tersimpan.</p>
  <p style="font-weight:700;margin-bottom:20px;font-size:15px" id="deleteTargetName"></p>
  <div class="form-actions">
    <button id="confirmDeleteBtn" class="danger"><svg class="svg-icon"><use href="#i-trash"></use></svg> Ya, Hapus Tenant</button>
    <button id="closeDeleteModal" class="ghost">Batal</button>
  </div>
</div>
</div>

<div id="toast"></div>

<script>
/* State */
let token=sessionStorage.getItem("adminToken")||"";
let tenants=[];
let selectedCode="";
let tenantDetail=null;
let activeTab="overview";
document.getElementById("token").value=token;

/* Utils */
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function svgIcon(name){return `<svg class="svg-icon"><use href="#i-${name}"></use></svg>`}
function toast(msg,type="info"){
  const el=document.createElement("div");el.className="toast-item "+type;el.textContent=msg;
  document.getElementById("toast").appendChild(el);setTimeout(()=>el.remove(),3100);
}
async function api(path,opts={}){
  const h=Object.assign({},opts.headers||{});if(token)h["X-Admin-Token"]=token;
  const res=await fetch(path,Object.assign({},opts,{headers:h}));
  const txt=await res.text();let body={};
  try{body=txt?JSON.parse(txt):{}}catch{body={detail:txt}}
  if(!res.ok)throw new Error(body.detail||txt||res.status);return body;
}

/* Tenants */
async function loadTenants(){
  try{const data=await api("/admin/api/tenants");tenants=data.tenants||[];renderTenantList();
    if(!selectedCode&&tenants[0])selectTenant(tenants[0].client_code);
    else if(selectedCode)selectTenant(selectedCode);
  }catch(e){toast(e.message,"error")}
}
function renderTenantList(){
  const list=document.getElementById("tenantList");
  const q=(document.getElementById("searchTenant").value||"").toLowerCase();
  const filtered=tenants.filter(t=>(t.brand_name+t.client_code).toLowerCase().includes(q));
  if(!filtered.length){list.innerHTML='<div style="padding:20px;text-align:center;color:var(--muted);font-size:12px">Tidak ada tenant</div>';return}
  list.innerHTML=filtered.map(t=>`
    <button class="tenant-item ${t.client_code===selectedCode?"selected":""}" data-code="${esc(t.client_code)}">
      <div class="t-row"><span class="t-name">${esc(t.brand_name)}</span><span class="badge ${esc(t.status)}">${esc(t.status)}</span></div>
      <div class="t-code">${esc(t.client_code)} · ${esc(t.active_channel)}</div>
    </button>`).join("");
  list.querySelectorAll(".tenant-item").forEach(b=>b.addEventListener("click",()=>selectTenant(b.dataset.code)));
}

async function selectTenant(code){
  selectedCode=code;renderTenantList();
  document.getElementById("pageTitle").textContent=tenants.find(t=>t.client_code===code)?.brand_name||code;
  try{tenantDetail=await api(`/admin/api/tenants/${code}/detail`);renderDetail()}
  catch(e){document.getElementById("mainContent").innerHTML=`<div class="empty-state"><div class="icon">${svgIcon("alert")}</div><p>${esc(e.message)}</p></div>`}
}

/* Render Detail */
function renderDetail(){
  if(!tenantDetail)return;
  const d=tenantDetail,c=d.configs.client,ch=d.configs.channel,ai=d.configs.ai,pay=d.configs.payment,ff=d.configs.feature_flags,s=d.stats,ak=d.api_keys;
  const main=document.getElementById("mainContent");
  main.innerHTML=`
<!-- Header -->
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:24px;flex-wrap:wrap;gap:12px">
  <div style="display:flex;align-items:center;gap:16px">
    <div style="width:48px;height:48px;border-radius:14px;background:linear-gradient(135deg,var(--primary),var(--purple));display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:20px">${esc((c.brand_name||"T")[0].toUpperCase())}</div>
    <div>
      <h2 style="font-size:20px;font-weight:800">${esc(c.brand_name||d.client_code)}</h2>
      <div style="display:flex;gap:8px;align-items:center;margin-top:4px">
        <span class="badge ${esc(d.status)}">${esc(d.status)}</span>
        <span style="font-size:12px;color:var(--muted)">${esc(d.client_code)}</span>
      </div>
    </div>
  </div>
  <div style="display:flex;gap:8px">
    <a href="${esc(d.urls.dashboard)}" target="_blank" style="text-decoration:none"><button class="primary sm">${svgIcon("dashboard")} Dashboard</button></a>
    <button class="danger sm" onclick="openDeleteModal()">${svgIcon("trash")} Hapus</button>
  </div>
</div>

<!-- Stats -->
<div class="stats-row">
  <div class="stat-card"><div class="stat-icon blue">${svgIcon("message")}</div><div class="stat-info"><div class="stat-value">${s.conversations}</div><div class="stat-label">Conversations</div></div></div>
  <div class="stat-card"><div class="stat-icon green">${svgIcon("users")}</div><div class="stat-info"><div class="stat-value">${s.contacts}</div><div class="stat-label">Contacts</div></div></div>
  <div class="stat-card"><div class="stat-icon purple">${svgIcon("mail")}</div><div class="stat-info"><div class="stat-value">${s.messages}</div><div class="stat-label">Messages</div></div></div>
  <div class="stat-card"><div class="stat-icon amber">${svgIcon("box")}</div><div class="stat-info"><div class="stat-value">${s.bookings}</div><div class="stat-label">Bookings</div></div></div>
  <div class="stat-card"><div class="stat-icon blue">${svgIcon("book")}</div><div class="stat-info"><div class="stat-value">${s.knowledge_docs}</div><div class="stat-label">Knowledge</div></div></div>
</div>

<!-- Tabs -->
<div class="tabs" id="tabs">
  <button class="tab-btn ${activeTab==="overview"?"active":""}" data-tab="overview">Overview</button>
  <button class="tab-btn ${activeTab==="apikeys"?"active":""}" data-tab="apikeys">API Keys</button>
  <button class="tab-btn ${activeTab==="channel"?"active":""}" data-tab="channel">Channel</button>
  <button class="tab-btn ${activeTab==="ai"?"active":""}" data-tab="ai">AI Config</button>
  <button class="tab-btn ${activeTab==="payment"?"active":""}" data-tab="payment">Payment</button>
  <button class="tab-btn ${activeTab==="flags"?"active":""}" data-tab="flags">Feature Flags</button>
  <button class="tab-btn ${activeTab==="raw"?"active":""}" data-tab="raw">Raw JSON</button>
</div>

<!-- Overview Tab -->
<div class="tab-panel ${activeTab==="overview"?"active":""}" id="panel-overview">
  <div class="card">
    <div class="card-header"><h3>Informasi Tenant</h3></div>
    <div class="form-grid">
      <div class="form-group"><div class="form-label">Brand Name</div><input id="oBrandName" value="${esc(c.brand_name||"")}"></div>
      <div class="form-group"><div class="form-label">Bot Name</div><input id="oBotName" value="${esc(c.bot_name||"")}"></div>
      <div class="form-group"><div class="form-label">Status</div><select id="oStatus"><option value="active" ${d.status==="active"?"selected":""}>Active</option><option value="inactive" ${d.status==="inactive"?"selected":""}>Inactive</option></select></div>
      <div class="form-group"><div class="form-label">Admin Phone</div><input id="oAdminPhone" value="${esc(c.admin_notification_phone||c.admin_group_id||"")}"></div>
      <div class="form-group"><div class="form-label">Default User Call</div><input id="oUserCall" value="${esc(c.default_user_call||"")}"></div>
      <div class="form-group"><div class="form-label">Tone</div><input id="oTone" value="${esc(c.tone||"")}"></div>
      <div class="form-group"><div class="form-label">Timezone</div><input id="oTimezone" value="${esc(c.timezone||"Asia/Jakarta")}"></div>
      <div class="form-group"><div class="form-label">Language</div><input id="oLang" value="${esc(c.default_language||"id")}"></div>
    </div>
    <div class="form-actions">
      <button class="primary" onclick="saveOverview()">${svgIcon("save")} Simpan Perubahan</button>
      <button class="${d.status==="active"?"danger":"success"}" onclick="toggleStatus()">${d.status==="active"?svgIcon("pause")+" Nonaktifkan":svgIcon("play")+" Aktifkan"}</button>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><h3>Webhook URLs</h3><span class="subtitle">Gunakan URL berikut untuk webhook</span></div>
    <div style="display:grid;gap:12px">
      <div><div class="form-label" style="margin-bottom:6px">StarSendr Webhook</div><div class="url-display">${esc(location.origin+d.urls.webhook_starsender)}</div></div>
      <div><div class="form-label" style="margin-bottom:6px">WABA Webhook</div><div class="url-display">${esc(location.origin+d.urls.webhook_waba)}</div></div>
    </div>
  </div>
</div>

<!-- API Keys Tab -->
<div class="tab-panel ${activeTab==="apikeys"?"active":""}" id="panel-apikeys">
  <div class="card">
    <div class="card-header"><h3 class="icon-title">${svgIcon("key")} API Keys</h3><span class="subtitle">Keys disimpan terenkripsi per-tenant</span></div>
    ${Object.entries(ak).map(([k,v])=>`
    <div class="key-row" style="display:grid;grid-template-columns:1fr 1fr;align-items:center;gap:20px;padding:16px 0;border-bottom:1px solid var(--border-light)">
      <div class="key-info" style="display:flex;flex-direction:column;gap:6px">
        <div class="key-name" style="font-weight:600;font-size:13px;color:var(--text)">${esc(k.replace(/_/g," ").toUpperCase())}</div>
        <div>
          ${v.is_set 
            ? `<span class="badge active" style="font-size:10px;padding:2px 8px">${esc(v.source)}: ${esc(v.masked_value)}</span>` 
            : `<span class="badge inactive" style="font-size:10px;padding:2px 8px">Belum diset</span>`}
        </div>
      </div>
      <div class="key-input-wrap" style="position:relative;display:flex;align-items:center">
        <input type="password" id="key_${esc(k)}" placeholder="${v.is_set ? '••••••••••••••••' : 'Masukkan key baru...'}" style="width:100%;padding-right:40px">
        <button class="sm ghost" onclick="toggleKeyVis('key_${esc(k)}')" title="Lihat key" style="position:absolute;right:4px;height:34px;width:34px;padding:0;border:none;background:transparent;display:flex;align-items:center;justify-content:center"><svg class="svg-icon"><use href="#i-eye"></use></svg></button>
      </div>
    </div>`).join("")}
    <div class="form-actions" style="margin-top:20px"><button class="primary" onclick="saveApiKeys()">${svgIcon("save")} Simpan API Keys</button></div>
  </div>
</div>

<!-- Channel Tab -->
<div class="tab-panel ${activeTab==="channel"?"active":""}" id="panel-channel">
  <div class="card">
    <div class="card-header"><h3 class="icon-title">${svgIcon("radio")} Channel Config</h3></div>
    <div class="form-grid" style="margin-bottom:16px">
      <div class="form-group"><div class="form-label">Active Channel</div>
        <select id="chActive">${["starsender","waba","telegram"].map(c2=>`<option value="${c2}" ${ch.active_channel===c2?"selected":""}>${c2}</option>`).join("")}</select>
      </div>
    </div>
    ${Object.entries(ch.channels||{}).map(([chName,chConf])=>`
    <div style="margin-top:16px;padding-top:16px;border-top:2px solid var(--border-light)">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:12px">
        <span style="font-weight:700;font-size:14px;text-transform:capitalize">${esc(chName)}</span>
        <div class="toggle ${chConf.enabled?"on":""}" data-channel="${esc(chName)}" onclick="toggleChannel(this)"></div>
      </div>
      <div class="form-grid">
        ${Object.entries(chConf).filter(([k2])=>k2!=="enabled").map(([k2,v2])=>`
        <div class="form-group"><div class="form-label">${esc(k2)}</div><input class="ch-field" data-channel="${esc(chName)}" data-key="${esc(k2)}" value="${esc(v2)}"></div>`).join("")}
      </div>
    </div>`).join("")}
    <div class="form-actions"><button class="primary" onclick="saveChannel()">${svgIcon("save")} Simpan Channel</button></div>
  </div>
</div>

<!-- AI Tab -->
<div class="tab-panel ${activeTab==="ai"?"active":""}" id="panel-ai">
  <div class="card">
    <div class="card-header"><h3 class="icon-title">${svgIcon("bot")} AI Config</h3></div>
    <div class="form-grid">
      <div class="form-group"><div class="form-label">Provider</div><input id="aiProvider" value="${esc(ai.provider||"gemini")}"></div>
      <div class="form-group"><div class="form-label">Chat Model</div><input id="aiChatModel" value="${esc(ai.chat_model||"")}"></div>
      <div class="form-group"><div class="form-label">Temperature</div><input id="aiTemp" type="number" step="0.05" value="${ai.temperature??0.25}"></div>
      <div class="form-group"><div class="form-label">Max Output Tokens</div><input id="aiMaxTokens" type="number" value="${ai.max_output_tokens??650}"></div>
      <div class="form-group"><div class="form-label">Timeout (s)</div><input id="aiTimeout" type="number" value="${ai.timeout_seconds??25}"></div>
      <div class="form-group"><div class="form-label">Thinking Budget</div><input id="aiThinking" type="number" value="${ai.thinking_budget??0}"></div>
      <div class="form-group"><div class="form-label">Embedding Model</div><input id="aiEmbModel" value="${esc(ai.embedding_model||"")}"></div>
      <div class="form-group"><div class="form-label">Embedding Dim</div><input id="aiEmbDim" type="number" value="${ai.embedding_dimensions??1536}"></div>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><h3 class="icon-title">${svgIcon("shield")} Safety</h3></div>
    <div class="form-grid">
      <div class="form-group"><div class="form-label">Intent Confidence Min</div><input id="aiIntentMin" type="number" step="0.01" value="${(ai.safety||{}).intent_confidence_min??0.70}"></div>
      <div class="form-group"><div class="form-label">Retrieval Score Min</div><input id="aiRetrMin" type="number" step="0.01" value="${(ai.safety||{}).retrieval_score_min??0.55}"></div>
      <div class="form-group"><div class="form-label">Retrieval Confident</div><input id="aiRetrConf" type="number" step="0.01" value="${(ai.safety||{}).retrieval_score_confident??0.75}"></div>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><h3 class="icon-title">${svgIcon("zap")} Token Optimization</h3></div>
    <div class="form-grid">
      <div class="form-group"><div class="form-label">RAG Top K</div><input id="aiRagK" type="number" value="${(ai.token_optimization||{}).rag_top_k??3}"></div>
      <div class="form-group"><div class="form-label">Max Chunk Tokens</div><input id="aiChunkTok" type="number" value="${(ai.token_optimization||{}).max_chunk_tokens??500}"></div>
      <div class="form-group"><div class="form-label">Cache TTL (min)</div><input id="aiCacheTTL" type="number" value="${(ai.token_optimization||{}).response_cache_ttl_minutes??60}"></div>
    </div>
  </div>
  <div class="form-actions"><button class="primary" onclick="saveAI()">${svgIcon("save")} Simpan AI Config</button></div>
</div>

<!-- Payment Tab -->
<div class="tab-panel ${activeTab==="payment"?"active":""}" id="panel-payment">
  <div class="card">
    <div class="card-header"><h3 class="icon-title">${svgIcon("credit-card")} Payment Config</h3></div>
    <div class="form-grid">
      <div class="form-group full"><div class="form-label">Payment Instruction</div><textarea id="payInstruction" rows="3">${esc(pay.payment_instruction||"")}</textarea></div>
      <div class="form-group full"><div class="form-label">Verification Message</div><textarea id="payVerification" rows="3">${esc(pay.verification_message||"")}</textarea></div>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><h3 class="icon-title">${svgIcon("bank")} Bank Accounts</h3></div>
    ${(pay.bank_accounts||[]).length?`<table><thead><tr><th>Bank</th><th>Nomor</th><th>Atas Nama</th><th>Status</th></tr></thead><tbody>
    ${pay.bank_accounts.map(b=>`<tr><td style="font-weight:600">${esc(b.bank_name)}</td><td>${esc(b.account_number)}</td><td>${esc(b.account_holder)}</td><td><span class="badge ${b.is_active?"active":"inactive"}">${b.is_active?"Active":"Inactive"}</span></td></tr>`).join("")}
    </tbody></table>`:'<p style="color:var(--muted);text-align:center;padding:20px">Belum ada bank account</p>'}
  </div>
  <div class="form-actions"><button class="primary" onclick="savePayment()">${svgIcon("save")} Simpan Payment</button></div>
</div>

<!-- Flags Tab -->
<div class="tab-panel ${activeTab==="flags"?"active":""}" id="panel-flags">
  <div class="card">
    <div class="card-header"><h3 class="icon-title">${svgIcon("flag")} Feature Flags</h3><span class="subtitle">Toggle fitur on/off per tenant</span></div>
    ${Object.entries(ff).map(([k,v])=>`
    <div class="toggle-row">
      <span class="toggle-label">${esc(k.replace(/_/g," "))}</span>
      <div class="toggle ${v?"on":""}" data-flag="${esc(k)}" onclick="toggleFlag(this)"></div>
    </div>`).join("")}
    <div class="form-actions"><button class="primary" onclick="saveFlags()">${svgIcon("save")} Simpan Flags</button></div>
  </div>
</div>

<!-- Raw Tab -->
<div class="tab-panel ${activeTab==="raw"?"active":""}" id="panel-raw">
  <div class="card">
    <div class="card-header"><h3 class="icon-title">${svgIcon("code")} Raw JSON</h3>
      <select id="rawSection" onchange="loadRawSection()" style="width:180px">
        <option value="client">client_config</option>
        <option value="channel">channel_config</option>
        <option value="ai">ai_config</option>
        <option value="payment">payment_config</option>
        <option value="feature_flags">feature_flags</option>
      </select>
    </div>
    <textarea class="json-editor" id="rawEditor"></textarea>
    <div class="form-actions"><button class="primary" onclick="saveRaw()">${svgIcon("save")} Simpan</button></div>
  </div>
</div>`;
  main.querySelectorAll(".tab-btn").forEach(btn=>btn.addEventListener("click",()=>{
    activeTab=btn.dataset.tab;
    main.querySelectorAll(".tab-btn").forEach(b=>b.classList.toggle("active",b.dataset.tab===activeTab));
    main.querySelectorAll(".tab-panel").forEach(p=>p.classList.toggle("active",p.id==="panel-"+activeTab));
  }));
  loadRawSection();
}

/* Actions */
function toggleKeyVis(id){const el=document.getElementById(id);el.type=el.type==="password"?"text":"password"}
async function saveOverview(){
  try{await api(`/admin/api/tenants/${selectedCode}/config/client`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({config:{brand_name:document.getElementById("oBrandName").value.trim(),bot_name:document.getElementById("oBotName").value.trim(),status:document.getElementById("oStatus").value,admin_notification_phone:document.getElementById("oAdminPhone").value.trim(),default_user_call:document.getElementById("oUserCall").value.trim(),tone:document.getElementById("oTone").value.trim(),timezone:document.getElementById("oTimezone").value.trim(),default_language:document.getElementById("oLang").value.trim()}})});toast("Tersimpan!","success");await loadTenants();await selectTenant(selectedCode)}catch(e){toast(e.message,"error")}
}
async function toggleStatus(){
  if(!selectedCode)return;const action=tenantDetail.status==="active"?"disable":"enable";
  try{await api(`/admin/api/tenants/${selectedCode}/${action}`,{method:"POST"});toast("Status diubah","success");await loadTenants();await selectTenant(selectedCode)}catch(e){toast(e.message,"error")}
}
async function saveApiKeys(){
  const keys={};document.querySelectorAll("[id^='key_']").forEach(el=>{const k=el.id.replace("key_","");if(el.value.trim())keys[k]=el.value.trim()});
  if(!Object.keys(keys).length){toast("Tidak ada key yang diisi","error");return}
  try{await api(`/admin/api/tenants/${selectedCode}/api-keys`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({keys})});toast("API Keys disimpan","success");document.querySelectorAll("[id^='key_']").forEach(el=>el.value="");await selectTenant(selectedCode)}catch(e){toast(e.message,"error")}
}
function toggleChannel(el){el.classList.toggle("on")}
function toggleFlag(el){el.classList.toggle("on")}
async function saveChannel(){
  const channels={};document.querySelectorAll(".toggle[data-channel]").forEach(el=>{const ch=el.dataset.channel;channels[ch]=channels[ch]||{};channels[ch].enabled=el.classList.contains("on")});
  document.querySelectorAll(".ch-field").forEach(el=>{const ch=el.dataset.channel;channels[ch]=channels[ch]||{};channels[ch][el.dataset.key]=el.value.trim()});
  try{await api(`/admin/api/tenants/${selectedCode}/config/channel`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({config:{active_channel:document.getElementById("chActive").value,channels}})});toast("Channel disimpan","success");await loadTenants();await selectTenant(selectedCode)}catch(e){toast(e.message,"error")}
}
async function saveAI(){
  try{await api(`/admin/api/tenants/${selectedCode}/config/ai`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({config:{provider:document.getElementById("aiProvider").value.trim(),chat_model:document.getElementById("aiChatModel").value.trim(),temperature:parseFloat(document.getElementById("aiTemp").value)||0.25,max_output_tokens:parseInt(document.getElementById("aiMaxTokens").value)||650,timeout_seconds:parseInt(document.getElementById("aiTimeout").value)||25,thinking_budget:parseInt(document.getElementById("aiThinking").value)||0,embedding_model:document.getElementById("aiEmbModel").value.trim(),embedding_dimensions:parseInt(document.getElementById("aiEmbDim").value)||1536,safety:{intent_confidence_min:parseFloat(document.getElementById("aiIntentMin").value)||0.70,retrieval_score_min:parseFloat(document.getElementById("aiRetrMin").value)||0.55,retrieval_score_confident:parseFloat(document.getElementById("aiRetrConf").value)||0.75},token_optimization:{rag_top_k:parseInt(document.getElementById("aiRagK").value)||3,max_chunk_tokens:parseInt(document.getElementById("aiChunkTok").value)||500,response_cache_ttl_minutes:parseInt(document.getElementById("aiCacheTTL").value)||60}}})});toast("AI config disimpan","success");await selectTenant(selectedCode)}catch(e){toast(e.message,"error")}
}
async function savePayment(){
  try{await api(`/admin/api/tenants/${selectedCode}/config/payment`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({config:{payment_instruction:document.getElementById("payInstruction").value.trim(),verification_message:document.getElementById("payVerification").value.trim()}})});toast("Payment disimpan","success");await selectTenant(selectedCode)}catch(e){toast(e.message,"error")}
}
async function saveFlags(){
  const flags={};document.querySelectorAll(".toggle[data-flag]").forEach(el=>{flags[el.dataset.flag]=el.classList.contains("on")});
  try{await api(`/admin/api/tenants/${selectedCode}/config/feature_flags`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({config:flags})});toast("Flags disimpan","success");await selectTenant(selectedCode)}catch(e){toast(e.message,"error")}
}
function loadRawSection(){const s=document.getElementById("rawSection").value;document.getElementById("rawEditor").value=JSON.stringify(tenantDetail?.configs?.[s]||{},null,2)}
async function saveRaw(){
  const section=document.getElementById("rawSection").value;let config;
  try{config=JSON.parse(document.getElementById("rawEditor").value)}catch{toast("JSON tidak valid","error");return}
  try{await api(`/admin/api/tenants/${selectedCode}/config/${section}`,{method:"PUT",headers:{"Content-Type":"application/json"},body:JSON.stringify({config})});toast("Tersimpan","success");await selectTenant(selectedCode)}catch(e){toast(e.message,"error")}
}

/* Modals */
document.getElementById("openAddModal").addEventListener("click",()=>document.getElementById("addModal").classList.add("show"));
document.getElementById("closeAddModal").addEventListener("click",()=>document.getElementById("addModal").classList.remove("show"));
document.getElementById("addModal").addEventListener("click",e=>{if(e.target===e.currentTarget)e.currentTarget.classList.remove("show")});
document.getElementById("createTenantBtn").addEventListener("click",async()=>{
  const p={client_code:document.getElementById("newClientCode").value.trim(),brand_name:document.getElementById("newBrandName").value.trim(),bot_name:document.getElementById("newBotName").value.trim()||"Admin AI",admin_phone:document.getElementById("newAdminPhone").value.trim()};
  if(!p.client_code||!p.brand_name){toast("Client code dan brand name wajib","error");return}
  try{const d=await api("/admin/api/tenants",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(p)});selectedCode=d.client_code;document.getElementById("addModal").classList.remove("show");toast("Tenant dibuat!","success");await loadTenants()}catch(e){toast(e.message,"error")}
});
function openDeleteModal(){document.getElementById("deleteTargetName").textContent=`${tenantDetail?.configs?.client?.brand_name||selectedCode} (${selectedCode})`;document.getElementById("deleteModal").classList.add("show")}
document.getElementById("closeDeleteModal").addEventListener("click",()=>document.getElementById("deleteModal").classList.remove("show"));
document.getElementById("deleteModal").addEventListener("click",e=>{if(e.target===e.currentTarget)e.currentTarget.classList.remove("show")});
document.getElementById("confirmDeleteBtn").addEventListener("click",async()=>{
  try{await api(`/admin/api/tenants/${selectedCode}`,{method:"DELETE"});document.getElementById("deleteModal").classList.remove("show");toast("Tenant dihapus","success");selectedCode="";tenantDetail=null;document.getElementById("mainContent").innerHTML=`<div class="empty-state"><div class="icon">${svgIcon("clipboard")}</div><p>Pilih tenant</p></div>`;await loadTenants()}catch(e){toast(e.message,"error")}
});

/* Init */
document.getElementById("searchTenant").addEventListener("input",renderTenantList);
document.getElementById("saveToken").addEventListener("click",()=>{token=document.getElementById("token").value.trim();sessionStorage.setItem("adminToken",token);loadTenants()});
document.getElementById("refresh").addEventListener("click",loadTenants);
if(token)loadTenants();
</script>
</body>
</html>"""
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
