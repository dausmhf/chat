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
  --bg:#0c0e14;--bg2:#12141d;--panel:#181b27;--panel2:#1e2233;
  --glass:rgba(24,27,39,.72);--glass-border:rgba(255,255,255,.06);
  --line:#252a3a;--line-light:#2f3548;
  --text:#e8eaf0;--text2:#a0a8be;--muted:#636d85;
  --accent:#6c63ff;--accent2:#8b83ff;--accent-glow:rgba(108,99,255,.18);
  --green:#34d399;--green-bg:rgba(52,211,153,.12);--green-border:rgba(52,211,153,.25);
  --red:#f87171;--red-bg:rgba(248,113,113,.12);--red-border:rgba(248,113,113,.25);
  --amber:#fbbf24;--amber-bg:rgba(251,191,36,.10);--amber-border:rgba(251,191,36,.22);
  --blue:#60a5fa;--blue-bg:rgba(96,165,250,.10);
  --radius:10px;--radius-lg:14px;
  --shadow:0 2px 12px rgba(0,0,0,.28);
  --transition:all .2s cubic-bezier(.4,0,.2,1);
}
html{font-size:14px}
body{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow:hidden}

/* Toast */
#toast{position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px;pointer-events:none}
.toast-item{padding:10px 18px;border-radius:var(--radius);font-size:13px;font-weight:600;color:#fff;backdrop-filter:blur(12px);pointer-events:auto;animation:toastIn .3s ease,toastOut .3s ease 2.7s forwards;max-width:380px}
.toast-item.success{background:rgba(52,211,153,.85);border:1px solid var(--green)}
.toast-item.error{background:rgba(248,113,113,.85);border:1px solid var(--red)}
.toast-item.info{background:rgba(108,99,255,.85);border:1px solid var(--accent)}
@keyframes toastIn{from{opacity:0;transform:translateX(40px)}to{opacity:1;transform:translateX(0)}}
@keyframes toastOut{from{opacity:1}to{opacity:0;transform:translateY(-10px)}}

/* Layout */
.app{display:grid;grid-template-columns:320px 1fr;grid-template-rows:60px 1fr;height:100vh}
header{grid-column:1/-1;display:flex;align-items:center;justify-content:space-between;padding:0 20px;background:var(--panel);border-bottom:1px solid var(--line);z-index:10}
.logo{display:flex;align-items:center;gap:10px}
.logo h1{font-size:17px;font-weight:800;letter-spacing:-.3px}
.logo span{font-size:11px;font-weight:600;padding:3px 8px;border-radius:6px;background:var(--accent-glow);color:var(--accent2);border:1px solid rgba(108,99,255,.2)}
.header-actions{display:flex;align-items:center;gap:8px}

/* Inputs */
input,select,textarea{font-family:inherit;font-size:13px;color:var(--text);background:var(--bg2);border:1px solid var(--line);border-radius:var(--radius);padding:0 12px;height:38px;outline:none;transition:var(--transition)}
input:focus,select:focus,textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-glow)}
textarea{height:auto;min-height:80px;padding:10px 12px;resize:vertical}
input::placeholder{color:var(--muted)}

/* Buttons */
button{font-family:inherit;font-size:13px;font-weight:600;border:1px solid var(--line);border-radius:var(--radius);background:var(--panel2);color:var(--text);height:38px;padding:0 16px;cursor:pointer;transition:var(--transition);white-space:nowrap}
button:hover{background:var(--line);border-color:var(--line-light)}
button:active{transform:scale(.97)}
button:disabled{opacity:.4;cursor:not-allowed;transform:none}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}
button.primary:hover{background:var(--accent2);border-color:var(--accent2)}
button.danger{background:var(--red-bg);border-color:var(--red-border);color:var(--red)}
button.danger:hover{background:rgba(248,113,113,.2)}
button.success{background:var(--green-bg);border-color:var(--green-border);color:var(--green)}
button.ghost{background:transparent;border-color:transparent;color:var(--text2)}
button.ghost:hover{color:var(--text);background:var(--panel2)}
button.sm{height:32px;font-size:12px;padding:0 12px}

/* Sidebar */
.sidebar{background:var(--panel);border-right:1px solid var(--line);display:flex;flex-direction:column;overflow:hidden}
.sidebar-header{padding:14px 16px 10px;display:flex;flex-direction:column;gap:10px;border-bottom:1px solid var(--line)}
.sidebar-header h2{font-size:13px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.8px}
.search-wrap{position:relative}
.search-wrap input{width:100%;padding-left:36px;height:36px}
.search-wrap::before{content:"⌕";position:absolute;left:12px;top:50%;transform:translateY(-50%);color:var(--muted);font-size:15px}
.sidebar-list{flex:1;overflow-y:auto;overflow-x:hidden}
.tenant-item{width:100%;text-align:left;border:none;border-radius:0;border-bottom:1px solid var(--line);height:auto;padding:12px 16px;display:grid;gap:4px;cursor:pointer;transition:var(--transition)}
.tenant-item:hover{background:var(--panel2)}
.tenant-item.selected{background:var(--accent-glow);border-left:3px solid var(--accent)}
.tenant-item .t-row{display:flex;align-items:center;justify-content:space-between;gap:8px}
.tenant-item .t-name{font-weight:700;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tenant-item .t-code{font-size:11px;color:var(--muted);font-family:'Courier New',monospace}
.sidebar-footer{padding:12px 16px;border-top:1px solid var(--line)}

/* Badges */
.badge{display:inline-flex;align-items:center;gap:4px;padding:3px 10px;border-radius:999px;font-size:11px;font-weight:700;letter-spacing:.3px}
.badge.active{color:var(--green);background:var(--green-bg);border:1px solid var(--green-border)}
.badge.inactive{color:var(--red);background:var(--red-bg);border:1px solid var(--red-border)}
.badge::before{content:"";width:6px;height:6px;border-radius:50%;background:currentColor}

/* Content */
.content{overflow-y:auto;padding:24px;background:var(--bg)}

/* Tabs */
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:20px;overflow-x:auto;padding-bottom:0}
.tab-btn{border:none;border-radius:8px 8px 0 0;background:transparent;color:var(--muted);font-weight:600;padding:10px 18px;height:auto;font-size:13px;cursor:pointer;transition:var(--transition);border-bottom:2px solid transparent;white-space:nowrap}
.tab-btn:hover{color:var(--text);background:var(--panel2)}
.tab-btn.active{color:var(--accent2);border-bottom-color:var(--accent);background:var(--panel)}
.tab-panel{display:none}
.tab-panel.active{display:block;animation:fadeIn .25s ease}
@keyframes fadeIn{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:translateY(0)}}

/* Cards */
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-lg);padding:20px;margin-bottom:16px;box-shadow:var(--shadow)}
.card-header{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:16px}
.card-header h3{font-size:15px;font-weight:700}
.card-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:12px}

/* Stat Cards */
.stats-row{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:12px;margin-bottom:20px}
.stat-card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-lg);padding:16px;text-align:center;transition:var(--transition)}
.stat-card:hover{border-color:var(--accent);transform:translateY(-2px);box-shadow:0 4px 16px rgba(108,99,255,.12)}
.stat-card .stat-value{font-size:28px;font-weight:800;background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.stat-card .stat-label{font-size:11px;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.5px;margin-top:4px}

/* Form Grid */
.form-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:14px}
.form-group{display:flex;flex-direction:column;gap:5px}
.form-group.full{grid-column:1/-1}
.form-label{font-size:11px;font-weight:700;color:var(--text2);text-transform:uppercase;letter-spacing:.4px}
.form-row{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap}
.form-actions{display:flex;gap:8px;margin-top:16px;flex-wrap:wrap}

/* Key Row */
.key-row{display:grid;grid-template-columns:1fr auto;gap:8px;align-items:center;padding:12px 0;border-bottom:1px solid var(--line)}
.key-row:last-child{border-bottom:none}
.key-info{display:flex;flex-direction:column;gap:4px}
.key-name{font-weight:600;font-size:13px}
.key-source{font-size:11px;color:var(--muted)}
.key-source.set{color:var(--green)}
.key-source.not-set{color:var(--red)}
.key-input-wrap{display:flex;gap:6px;align-items:center}
.key-input-wrap input{width:280px}

/* Toggle */
.toggle-row{display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid var(--line)}
.toggle-row:last-child{border-bottom:none}
.toggle-label{font-weight:600;font-size:13px}
.toggle{position:relative;width:44px;height:24px;background:var(--line);border-radius:12px;cursor:pointer;transition:var(--transition);flex-shrink:0}
.toggle.on{background:var(--accent)}
.toggle::after{content:"";position:absolute;top:3px;left:3px;width:18px;height:18px;background:#fff;border-radius:50%;transition:var(--transition)}
.toggle.on::after{left:23px}

/* JSON Editor */
.json-editor{width:100%;min-height:300px;font-family:'Courier New',monospace;font-size:12px;line-height:1.6;background:var(--bg2);color:var(--green);border:1px solid var(--line);border-radius:var(--radius);padding:14px;resize:vertical}

/* Links */
.url-display{display:flex;align-items:center;gap:8px;padding:10px 14px;background:var(--bg2);border:1px solid var(--line);border-radius:var(--radius);font-family:'Courier New',monospace;font-size:12px;color:var(--blue);word-break:break-all}

/* Empty state */
.empty-state{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:12px;padding:60px 20px;color:var(--muted);text-align:center}
.empty-state .icon{font-size:48px;opacity:.3}
.empty-state p{font-size:14px;max-width:300px}

/* Modal */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.6);backdrop-filter:blur(6px);z-index:100;display:none;align-items:center;justify-content:center}
.modal-overlay.show{display:flex}
.modal{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius-lg);padding:28px;width:90%;max-width:540px;box-shadow:0 20px 60px rgba(0,0,0,.4);animation:modalIn .2s ease}
@keyframes modalIn{from{opacity:0;transform:scale(.95) translateY(10px)}to{opacity:1;transform:scale(1) translateY(0)}}
.modal h2{font-size:18px;font-weight:800;margin-bottom:20px}

/* Responsive */
@media(max-width:900px){
  .app{grid-template-columns:1fr;grid-template-rows:60px auto 1fr}
  .sidebar{max-height:40vh;border-right:none;border-bottom:1px solid var(--line)}
  .form-grid{grid-template-columns:1fr}
  .stats-row{grid-template-columns:repeat(2,1fr)}
  .key-input-wrap input{width:180px}
}
</style>
</head>
<body>
<div class="app">
<header>
  <div class="logo">
    <h1>HalloTravel</h1>
    <span>Super Admin</span>
  </div>
  <div class="header-actions">
    <input id="token" type="password" placeholder="Admin token" style="width:200px">
    <button id="saveToken" class="primary">Login</button>
    <button id="refresh" class="ghost">↻ Refresh</button>
  </div>
</header>
<aside class="sidebar">
  <div class="sidebar-header">
    <h2>Tenants</h2>
    <div class="search-wrap"><input id="searchTenant" type="text" placeholder="Cari tenant..."></div>
  </div>
  <div class="sidebar-list" id="tenantList"><div class="empty-state"><div class="icon">🔐</div><p>Masukkan admin token untuk memulai</p></div></div>
  <div class="sidebar-footer">
    <button id="openAddModal" class="primary" style="width:100%">＋ Tambah Tenant</button>
  </div>
</aside>
<main class="content" id="mainContent">
  <div class="empty-state"><div class="icon">📋</div><p>Pilih tenant dari sidebar untuk melihat detail</p></div>
</main>
</div>

<!-- Add Tenant Modal -->
<div class="modal-overlay" id="addModal">
<div class="modal">
  <h2>Tambah Tenant Baru</h2>
  <div class="form-grid">
    <div class="form-group"><div class="form-label">Client Code</div><input id="newClientCode" placeholder="travel_baru"></div>
    <div class="form-group"><div class="form-label">Brand Name</div><input id="newBrandName" placeholder="Nama Travel"></div>
    <div class="form-group"><div class="form-label">Bot Name</div><input id="newBotName" value="Admin AI"></div>
    <div class="form-group"><div class="form-label">Admin Phone</div><input id="newAdminPhone" placeholder="628xxx"></div>
  </div>
  <div class="form-actions">
    <button id="createTenantBtn" class="primary">Buat Tenant</button>
    <button id="closeAddModal" class="ghost">Batal</button>
  </div>
</div>
</div>

<!-- Confirm Delete Modal -->
<div class="modal-overlay" id="deleteModal">
<div class="modal">
  <h2 style="color:var(--red)">Hapus Tenant?</h2>
  <p style="color:var(--text2);margin-bottom:20px">Tenant akan dinonaktifkan dan folder config akan di-rename. Data di database tidak dihapus.</p>
  <p style="font-weight:700;margin-bottom:20px" id="deleteTargetName"></p>
  <div class="form-actions">
    <button id="confirmDeleteBtn" class="danger">Ya, Hapus</button>
    <button id="closeDeleteModal" class="ghost">Batal</button>
  </div>
</div>
</div>

<div id="toast"></div>

<script>
/* ========== State ========== */
let token = sessionStorage.getItem("adminToken") || "";
let tenants = [];
let selectedCode = "";
let tenantDetail = null;
let activeTab = "overview";
document.getElementById("token").value = token;

/* ========== Utils ========== */
function esc(v){return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function toast(msg,type="info"){
  const el=document.createElement("div");
  el.className="toast-item "+type;
  el.textContent=msg;
  document.getElementById("toast").appendChild(el);
  setTimeout(()=>el.remove(),3100);
}
async function api(path,opts={}){
  const h=Object.assign({},opts.headers||{});
  if(token)h["X-Admin-Token"]=token;
  const res=await fetch(path,Object.assign({},opts,{headers:h}));
  const txt=await res.text();
  let body={};
  try{body=txt?JSON.parse(txt):{}}catch{body={detail:txt}}
  if(!res.ok)throw new Error(body.detail||txt||res.status);
  return body;
}

/* ========== Tenants ========== */
async function loadTenants(){
  try{
    const data=await api("/admin/api/tenants");
    tenants=data.tenants||[];
    renderTenantList();
    if(!selectedCode&&tenants[0])selectTenant(tenants[0].client_code);
    else if(selectedCode)selectTenant(selectedCode);
  }catch(e){toast(e.message,"error")}
}
function renderTenantList(){
  const list=document.getElementById("tenantList");
  const q=(document.getElementById("searchTenant").value||"").toLowerCase();
  const filtered=tenants.filter(t=>(t.brand_name+t.client_code).toLowerCase().includes(q));
  if(!filtered.length){list.innerHTML='<div class="empty-state"><p>Tidak ada tenant ditemukan</p></div>';return}
  list.innerHTML=filtered.map(t=>`
    <button class="tenant-item ${t.client_code===selectedCode?"selected":""}" data-code="${esc(t.client_code)}">
      <div class="t-row"><span class="t-name">${esc(t.brand_name)}</span><span class="badge ${esc(t.status)}">${esc(t.status)}</span></div>
      <div class="t-code">${esc(t.client_code)} · ${esc(t.active_channel)}</div>
    </button>`).join("");
  list.querySelectorAll(".tenant-item").forEach(b=>b.addEventListener("click",()=>selectTenant(b.dataset.code)));
}

async function selectTenant(code){
  selectedCode=code;
  renderTenantList();
  try{
    tenantDetail=await api(`/admin/api/tenants/${code}/detail`);
    renderDetail();
  }catch(e){
    document.getElementById("mainContent").innerHTML=`<div class="empty-state"><div class="icon">⚠️</div><p>${esc(e.message)}</p></div>`;
  }
}

/* ========== Detail Render ========== */
function renderDetail(){
  if(!tenantDetail)return;
  const d=tenantDetail;
  const c=d.configs.client;
  const ch=d.configs.channel;
  const ai=d.configs.ai;
  const pay=d.configs.payment;
  const ff=d.configs.feature_flags;
  const s=d.stats;
  const ak=d.api_keys;

  const main=document.getElementById("mainContent");
  main.innerHTML=`
<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;flex-wrap:wrap;gap:10px">
  <div>
    <h2 style="font-size:22px;font-weight:800">${esc(c.brand_name||d.client_code)}</h2>
    <div style="display:flex;gap:8px;align-items:center;margin-top:6px">
      <span class="badge ${esc(d.status)}">${esc(d.status)}</span>
      <span style="font-size:12px;color:var(--muted);font-family:monospace">${esc(d.client_code)}</span>
    </div>
  </div>
  <div style="display:flex;gap:8px">
    <a href="${esc(d.urls.dashboard)}" target="_blank"><button class="sm primary">Dashboard Tenant →</button></a>
    <button class="sm danger" onclick="openDeleteModal()">Hapus</button>
  </div>
</div>
<div class="tabs" id="tabs">
  <button class="tab-btn ${activeTab==="overview"?"active":""}" data-tab="overview">Overview</button>
  <button class="tab-btn ${activeTab==="apikeys"?"active":""}" data-tab="apikeys">API Keys</button>
  <button class="tab-btn ${activeTab==="channel"?"active":""}" data-tab="channel">Channel</button>
  <button class="tab-btn ${activeTab==="ai"?"active":""}" data-tab="ai">AI Config</button>
  <button class="tab-btn ${activeTab==="payment"?"active":""}" data-tab="payment">Payment</button>
  <button class="tab-btn ${activeTab==="flags"?"active":""}" data-tab="flags">Feature Flags</button>
  <button class="tab-btn ${activeTab==="raw"?"active":""}" data-tab="raw">Raw JSON</button>
</div>

<!-- Overview -->
<div class="tab-panel ${activeTab==="overview"?"active":""}" id="panel-overview">
  <div class="stats-row">
    <div class="stat-card"><div class="stat-value">${s.conversations}</div><div class="stat-label">Conversations</div></div>
    <div class="stat-card"><div class="stat-value">${s.contacts}</div><div class="stat-label">Contacts</div></div>
    <div class="stat-card"><div class="stat-value">${s.messages}</div><div class="stat-label">Messages</div></div>
    <div class="stat-card"><div class="stat-value">${s.bookings}</div><div class="stat-label">Bookings</div></div>
    <div class="stat-card"><div class="stat-value">${s.knowledge_docs}</div><div class="stat-label">Knowledge Docs</div></div>
  </div>
  <div class="card">
    <div class="card-header"><h3>Informasi Tenant</h3></div>
    <div class="form-grid">
      <div class="form-group"><div class="form-label">Brand Name</div><input id="oBrandName" value="${esc(c.brand_name||"")}"></div>
      <div class="form-group"><div class="form-label">Bot Name</div><input id="oBotName" value="${esc(c.bot_name||"")}"></div>
      <div class="form-group"><div class="form-label">Status</div>
        <select id="oStatus"><option value="active" ${d.status==="active"?"selected":""}>Active</option><option value="inactive" ${d.status==="inactive"?"selected":""}>Inactive</option></select>
      </div>
      <div class="form-group"><div class="form-label">Admin Phone</div><input id="oAdminPhone" value="${esc(c.admin_notification_phone||c.admin_group_id||"")}"></div>
      <div class="form-group"><div class="form-label">Default User Call</div><input id="oUserCall" value="${esc(c.default_user_call||"")}"></div>
      <div class="form-group"><div class="form-label">Tone</div><input id="oTone" value="${esc(c.tone||"")}"></div>
      <div class="form-group"><div class="form-label">Timezone</div><input id="oTimezone" value="${esc(c.timezone||"Asia/Jakarta")}"></div>
      <div class="form-group"><div class="form-label">Language</div><input id="oLang" value="${esc(c.default_language||"id")}"></div>
    </div>
    <div class="form-actions">
      <button class="primary" onclick="saveOverview()">Simpan</button>
      <button class="${d.status==="active"?"danger":"success"}" onclick="toggleStatus()">${d.status==="active"?"Nonaktifkan":"Aktifkan"}</button>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><h3>Webhook URLs</h3></div>
    <div style="display:grid;gap:10px">
      <div><div class="form-label" style="margin-bottom:4px">StarSendr Webhook</div><div class="url-display">${esc(location.origin+d.urls.webhook_starsender)}</div></div>
      <div><div class="form-label" style="margin-bottom:4px">WABA Webhook</div><div class="url-display">${esc(location.origin+d.urls.webhook_waba)}</div></div>
    </div>
  </div>
</div>

<!-- API Keys -->
<div class="tab-panel ${activeTab==="apikeys"?"active":""}" id="panel-apikeys">
  <div class="card">
    <div class="card-header"><h3>API Keys</h3><span style="font-size:11px;color:var(--muted)">Keys disimpan terenkripsi per-tenant</span></div>
    ${Object.entries(ak).map(([k,v])=>`
    <div class="key-row">
      <div class="key-info">
        <div class="key-name">${esc(k.replace(/_/g," ").replace(/\\b\\w/g,c=>c.toUpperCase()))}</div>
        <div class="key-source ${v.is_set?"set":"not-set"}">${v.is_set?`✓ Set (${esc(v.source)}) · ${esc(v.masked_value)}`:"✗ Belum diset"}</div>
      </div>
      <div class="key-input-wrap">
        <input type="password" id="key_${esc(k)}" placeholder="Masukkan ${esc(k)}">
        <button class="sm ghost" onclick="toggleKeyVis('key_${esc(k)}')">👁</button>
      </div>
    </div>`).join("")}
    <div class="form-actions"><button class="primary" onclick="saveApiKeys()">Simpan API Keys</button></div>
  </div>
</div>

<!-- Channel -->
<div class="tab-panel ${activeTab==="channel"?"active":""}" id="panel-channel">
  <div class="card">
    <div class="card-header"><h3>Channel Config</h3></div>
    <div class="form-grid">
      <div class="form-group"><div class="form-label">Active Channel</div>
        <select id="chActive">${["starsender","waba","telegram"].map(c2=>`<option value="${c2}" ${ch.active_channel===c2?"selected":""}>${c2}</option>`).join("")}</select>
      </div>
    </div>
    ${Object.entries(ch.channels||{}).map(([chName,chConf])=>`
    <div style="margin-top:16px;padding-top:16px;border-top:1px solid var(--line)">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <span style="font-weight:700;text-transform:capitalize">${esc(chName)}</span>
        <div class="toggle ${chConf.enabled?"on":""}" data-channel="${esc(chName)}" onclick="toggleChannel(this)"></div>
      </div>
      <div class="form-grid">
        ${Object.entries(chConf).filter(([k2])=>k2!=="enabled").map(([k2,v2])=>`
        <div class="form-group"><div class="form-label">${esc(k2)}</div><input class="ch-field" data-channel="${esc(chName)}" data-key="${esc(k2)}" value="${esc(v2)}"></div>`).join("")}
      </div>
    </div>`).join("")}
    <div class="form-actions"><button class="primary" onclick="saveChannel()">Simpan Channel</button></div>
  </div>
</div>

<!-- AI Config -->
<div class="tab-panel ${activeTab==="ai"?"active":""}" id="panel-ai">
  <div class="card">
    <div class="card-header"><h3>AI Config</h3></div>
    <div class="form-grid">
      <div class="form-group"><div class="form-label">Provider</div><input id="aiProvider" value="${esc(ai.provider||"gemini")}"></div>
      <div class="form-group"><div class="form-label">Chat Model</div><input id="aiChatModel" value="${esc(ai.chat_model||"")}"></div>
      <div class="form-group"><div class="form-label">Temperature</div><input id="aiTemp" type="number" step="0.05" value="${ai.temperature??0.25}"></div>
      <div class="form-group"><div class="form-label">Max Output Tokens</div><input id="aiMaxTokens" type="number" value="${ai.max_output_tokens??650}"></div>
      <div class="form-group"><div class="form-label">Timeout (seconds)</div><input id="aiTimeout" type="number" value="${ai.timeout_seconds??25}"></div>
      <div class="form-group"><div class="form-label">Thinking Budget</div><input id="aiThinking" type="number" value="${ai.thinking_budget??0}"></div>
      <div class="form-group"><div class="form-label">Embedding Model</div><input id="aiEmbModel" value="${esc(ai.embedding_model||"")}"></div>
      <div class="form-group"><div class="form-label">Embedding Dimensions</div><input id="aiEmbDim" type="number" value="${ai.embedding_dimensions??1536}"></div>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><h3>Safety Thresholds</h3></div>
    <div class="form-grid">
      <div class="form-group"><div class="form-label">Intent Confidence Min</div><input id="aiIntentMin" type="number" step="0.01" value="${(ai.safety||{}).intent_confidence_min??0.70}"></div>
      <div class="form-group"><div class="form-label">Retrieval Score Min</div><input id="aiRetrMin" type="number" step="0.01" value="${(ai.safety||{}).retrieval_score_min??0.55}"></div>
      <div class="form-group"><div class="form-label">Retrieval Score Confident</div><input id="aiRetrConf" type="number" step="0.01" value="${(ai.safety||{}).retrieval_score_confident??0.75}"></div>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><h3>Token Optimization</h3></div>
    <div class="form-grid">
      <div class="form-group"><div class="form-label">RAG Top K</div><input id="aiRagK" type="number" value="${(ai.token_optimization||{}).rag_top_k??3}"></div>
      <div class="form-group"><div class="form-label">Max Chunk Tokens</div><input id="aiChunkTok" type="number" value="${(ai.token_optimization||{}).max_chunk_tokens??500}"></div>
      <div class="form-group"><div class="form-label">Cache TTL (minutes)</div><input id="aiCacheTTL" type="number" value="${(ai.token_optimization||{}).response_cache_ttl_minutes??60}"></div>
    </div>
  </div>
  <div class="form-actions"><button class="primary" onclick="saveAI()">Simpan AI Config</button></div>
</div>

<!-- Payment -->
<div class="tab-panel ${activeTab==="payment"?"active":""}" id="panel-payment">
  <div class="card">
    <div class="card-header"><h3>Payment Config</h3></div>
    <div class="form-grid">
      <div class="form-group full"><div class="form-label">Payment Instruction</div><textarea id="payInstruction" rows="3">${esc(pay.payment_instruction||"")}</textarea></div>
      <div class="form-group full"><div class="form-label">Verification Message</div><textarea id="payVerification" rows="3">${esc(pay.verification_message||"")}</textarea></div>
    </div>
  </div>
  <div class="card">
    <div class="card-header"><h3>Bank Accounts</h3></div>
    ${(pay.bank_accounts||[]).length?pay.bank_accounts.map((b,i)=>`
    <div style="padding:10px 0;border-bottom:1px solid var(--line)">
      <strong>${esc(b.bank_name)}</strong> — ${esc(b.account_number)} (${esc(b.account_holder)})
      <span class="badge ${b.is_active?"active":"inactive"}" style="margin-left:8px">${b.is_active?"Active":"Inactive"}</span>
    </div>`).join(""):'<p style="color:var(--muted)">Belum ada bank account.</p>'}
  </div>
  <div class="form-actions"><button class="primary" onclick="savePayment()">Simpan Payment</button></div>
</div>

<!-- Feature Flags -->
<div class="tab-panel ${activeTab==="flags"?"active":""}" id="panel-flags">
  <div class="card">
    <div class="card-header"><h3>Feature Flags</h3></div>
    ${Object.entries(ff).map(([k,v])=>`
    <div class="toggle-row">
      <span class="toggle-label">${esc(k.replace(/_/g," ").replace(/\\b\\w/g,c=>c.toUpperCase()))}</span>
      <div class="toggle ${v?"on":""}" data-flag="${esc(k)}" onclick="toggleFlag(this)"></div>
    </div>`).join("")}
    <div class="form-actions"><button class="primary" onclick="saveFlags()">Simpan Flags</button></div>
  </div>
</div>

<!-- Raw JSON -->
<div class="tab-panel ${activeTab==="raw"?"active":""}" id="panel-raw">
  <div class="card">
    <div class="card-header"><h3>Raw JSON Config</h3>
      <select id="rawSection" onchange="loadRawSection()">
        <option value="client">client_config</option>
        <option value="channel">channel_config</option>
        <option value="ai">ai_config</option>
        <option value="payment">payment_config</option>
        <option value="feature_flags">feature_flags</option>
      </select>
    </div>
    <textarea class="json-editor" id="rawEditor"></textarea>
    <div class="form-actions"><button class="primary" onclick="saveRaw()">Simpan Raw JSON</button></div>
  </div>
</div>`;

  // Tab switching
  main.querySelectorAll(".tab-btn").forEach(btn=>btn.addEventListener("click",()=>{
    activeTab=btn.dataset.tab;
    main.querySelectorAll(".tab-btn").forEach(b=>b.classList.toggle("active",b.dataset.tab===activeTab));
    main.querySelectorAll(".tab-panel").forEach(p=>p.classList.toggle("active",p.id==="panel-"+activeTab));
  }));
  loadRawSection();
}

/* ========== Actions ========== */
function toggleKeyVis(id){const el=document.getElementById(id);el.type=el.type==="password"?"text":"password"}

async function saveOverview(){
  try{
    await api(`/admin/api/tenants/${selectedCode}/config/client`,{
      method:"PUT",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({config:{
        brand_name:document.getElementById("oBrandName").value.trim(),
        bot_name:document.getElementById("oBotName").value.trim(),
        status:document.getElementById("oStatus").value,
        admin_notification_phone:document.getElementById("oAdminPhone").value.trim(),
        default_user_call:document.getElementById("oUserCall").value.trim(),
        tone:document.getElementById("oTone").value.trim(),
        timezone:document.getElementById("oTimezone").value.trim(),
        default_language:document.getElementById("oLang").value.trim(),
      }})
    });
    toast("Overview disimpan","success");
    await loadTenants();
    await selectTenant(selectedCode);
  }catch(e){toast(e.message,"error")}
}

async function toggleStatus(){
  if(!selectedCode)return;
  const action=tenantDetail.status==="active"?"disable":"enable";
  try{
    await api(`/admin/api/tenants/${selectedCode}/${action}`,{method:"POST"});
    toast(`Tenant ${action==="enable"?"diaktifkan":"dinonaktifkan"}`,"success");
    await loadTenants();
    await selectTenant(selectedCode);
  }catch(e){toast(e.message,"error")}
}

async function saveApiKeys(){
  const keys={};
  document.querySelectorAll("[id^='key_']").forEach(el=>{
    const k=el.id.replace("key_","");
    if(el.value.trim())keys[k]=el.value.trim();
  });
  if(!Object.keys(keys).length){toast("Tidak ada key yang diisi","error");return}
  try{
    await api(`/admin/api/tenants/${selectedCode}/api-keys`,{
      method:"PUT",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({keys})
    });
    toast("API Keys disimpan","success");
    document.querySelectorAll("[id^='key_']").forEach(el=>el.value="");
    await selectTenant(selectedCode);
  }catch(e){toast(e.message,"error")}
}

function toggleChannel(el){el.classList.toggle("on")}
function toggleFlag(el){el.classList.toggle("on")}

async function saveChannel(){
  const channels={};
  document.querySelectorAll(".toggle[data-channel]").forEach(el=>{
    const ch=el.dataset.channel;
    channels[ch]=channels[ch]||{};
    channels[ch].enabled=el.classList.contains("on");
  });
  document.querySelectorAll(".ch-field").forEach(el=>{
    const ch=el.dataset.channel;
    channels[ch]=channels[ch]||{};
    channels[ch][el.dataset.key]=el.value.trim();
  });
  try{
    await api(`/admin/api/tenants/${selectedCode}/config/channel`,{
      method:"PUT",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({config:{active_channel:document.getElementById("chActive").value,channels}})
    });
    toast("Channel config disimpan","success");
    await loadTenants();
    await selectTenant(selectedCode);
  }catch(e){toast(e.message,"error")}
}

async function saveAI(){
  try{
    await api(`/admin/api/tenants/${selectedCode}/config/ai`,{
      method:"PUT",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({config:{
        provider:document.getElementById("aiProvider").value.trim(),
        chat_model:document.getElementById("aiChatModel").value.trim(),
        temperature:parseFloat(document.getElementById("aiTemp").value)||0.25,
        max_output_tokens:parseInt(document.getElementById("aiMaxTokens").value)||650,
        timeout_seconds:parseInt(document.getElementById("aiTimeout").value)||25,
        thinking_budget:parseInt(document.getElementById("aiThinking").value)||0,
        embedding_model:document.getElementById("aiEmbModel").value.trim(),
        embedding_dimensions:parseInt(document.getElementById("aiEmbDim").value)||1536,
        safety:{
          intent_confidence_min:parseFloat(document.getElementById("aiIntentMin").value)||0.70,
          retrieval_score_min:parseFloat(document.getElementById("aiRetrMin").value)||0.55,
          retrieval_score_confident:parseFloat(document.getElementById("aiRetrConf").value)||0.75,
        },
        token_optimization:{
          rag_top_k:parseInt(document.getElementById("aiRagK").value)||3,
          max_chunk_tokens:parseInt(document.getElementById("aiChunkTok").value)||500,
          response_cache_ttl_minutes:parseInt(document.getElementById("aiCacheTTL").value)||60,
        }
      }})
    });
    toast("AI config disimpan","success");
    await selectTenant(selectedCode);
  }catch(e){toast(e.message,"error")}
}

async function savePayment(){
  try{
    await api(`/admin/api/tenants/${selectedCode}/config/payment`,{
      method:"PUT",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({config:{
        payment_instruction:document.getElementById("payInstruction").value.trim(),
        verification_message:document.getElementById("payVerification").value.trim(),
      }})
    });
    toast("Payment config disimpan","success");
    await selectTenant(selectedCode);
  }catch(e){toast(e.message,"error")}
}

async function saveFlags(){
  const flags={};
  document.querySelectorAll(".toggle[data-flag]").forEach(el=>{flags[el.dataset.flag]=el.classList.contains("on")});
  try{
    await api(`/admin/api/tenants/${selectedCode}/config/feature_flags`,{
      method:"PUT",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({config:flags})
    });
    toast("Feature flags disimpan","success");
    await selectTenant(selectedCode);
  }catch(e){toast(e.message,"error")}
}

function loadRawSection(){
  const section=document.getElementById("rawSection").value;
  const data=tenantDetail?.configs?.[section]||{};
  document.getElementById("rawEditor").value=JSON.stringify(data,null,2);
}
async function saveRaw(){
  const section=document.getElementById("rawSection").value;
  let config;
  try{config=JSON.parse(document.getElementById("rawEditor").value)}catch{toast("JSON tidak valid","error");return}
  try{
    await api(`/admin/api/tenants/${selectedCode}/config/${section}`,{
      method:"PUT",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({config})
    });
    toast(`${section} disimpan`,"success");
    await selectTenant(selectedCode);
  }catch(e){toast(e.message,"error")}
}

/* ========== Create Tenant ========== */
document.getElementById("openAddModal").addEventListener("click",()=>document.getElementById("addModal").classList.add("show"));
document.getElementById("closeAddModal").addEventListener("click",()=>document.getElementById("addModal").classList.remove("show"));
document.getElementById("addModal").addEventListener("click",e=>{if(e.target===e.currentTarget)e.currentTarget.classList.remove("show")});
document.getElementById("createTenantBtn").addEventListener("click",async()=>{
  const payload={
    client_code:document.getElementById("newClientCode").value.trim(),
    brand_name:document.getElementById("newBrandName").value.trim(),
    bot_name:document.getElementById("newBotName").value.trim()||"Admin AI",
    admin_phone:document.getElementById("newAdminPhone").value.trim(),
  };
  if(!payload.client_code||!payload.brand_name){toast("Client code dan brand name wajib diisi","error");return}
  try{
    const data=await api("/admin/api/tenants",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(payload)});
    selectedCode=data.client_code;
    document.getElementById("addModal").classList.remove("show");
    toast("Tenant berhasil dibuat","success");
    await loadTenants();
  }catch(e){toast(e.message,"error")}
});

/* ========== Delete Tenant ========== */
function openDeleteModal(){
  document.getElementById("deleteTargetName").textContent=`${tenantDetail?.configs?.client?.brand_name||selectedCode} (${selectedCode})`;
  document.getElementById("deleteModal").classList.add("show");
}
document.getElementById("closeDeleteModal").addEventListener("click",()=>document.getElementById("deleteModal").classList.remove("show"));
document.getElementById("deleteModal").addEventListener("click",e=>{if(e.target===e.currentTarget)e.currentTarget.classList.remove("show")});
document.getElementById("confirmDeleteBtn").addEventListener("click",async()=>{
  try{
    await api(`/admin/api/tenants/${selectedCode}`,{method:"DELETE"});
    document.getElementById("deleteModal").classList.remove("show");
    toast("Tenant dihapus","success");
    selectedCode="";
    tenantDetail=null;
    document.getElementById("mainContent").innerHTML='<div class="empty-state"><div class="icon">📋</div><p>Pilih tenant dari sidebar untuk melihat detail</p></div>';
    await loadTenants();
  }catch(e){toast(e.message,"error")}
});

/* ========== Init ========== */
document.getElementById("searchTenant").addEventListener("input",renderTenantList);
document.getElementById("saveToken").addEventListener("click",()=>{
  token=document.getElementById("token").value.trim();
  sessionStorage.setItem("adminToken",token);
  loadTenants();
});
document.getElementById("refresh").addEventListener("click",loadTenants);
if(token)loadTenants();
</script>
</body>
</html>"""")

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
