import os
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.admin.admin_command_service import execute_bot_on, execute_takeover
from app.admin.tenant_service import create_tenant
from app.config import client_config_manager, settings
from app.ingestion.event_handler import get_or_create_client_uuid
from app.rag.ingestion_service import ingest_text_knowledge, list_knowledge_documents
from app.storage import models
from app.storage.database import get_db

router = APIRouter()


class TenantCreateRequest(BaseModel):
    client_code: str = Field(min_length=2, max_length=80)
    brand_name: str = Field(min_length=2, max_length=180)
    bot_name: str = Field(default="Admin AI", max_length=180)
    admin_phone: Optional[str] = None
    starsender_api_key_env: str = "STARSENDER_API_KEY"
    gemini_api_key_env: str = "GEMINI_API_KEY"


class KnowledgeTextRequest(BaseModel):
    title: str = Field(min_length=2, max_length=220)
    text: str = Field(min_length=20)
    source_type: str = "admin_override"
    document_type: str = "faq"
    doc_priority: int = Field(default=70, ge=1, le=100)
    metadata: Dict[str, Any] = Field(default_factory=dict)


def require_admin_token(request: Request, client_code: Optional[str] = None, x_admin_token: Optional[str] = Header(default=None)) -> None:
    expected = _admin_token_for_client(client_code or "")
    if not expected and settings.app_env == "production":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Admin dashboard token is not configured.",
        )
    if not expected:
        return

    provided = x_admin_token or request.query_params.get("token") or ""
    if provided != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin token.")


@router.get("/admin/{client_code}/dashboard", response_class=HTMLResponse)
async def admin_dashboard(client_code: str):
    _ensure_client_exists(client_code)
    return HTMLResponse(_dashboard_html(client_code))


@router.post("/admin/api/tenants")
async def create_tenant_endpoint(
    body: TenantCreateRequest,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    try:
        return create_tenant(
            db,
            client_code=body.client_code,
            brand_name=body.brand_name,
            bot_name=body.bot_name,
            admin_phone=body.admin_phone,
            starsender_api_key_env=body.starsender_api_key_env,
            gemini_api_key_env=body.gemini_api_key_env,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/admin/{client_code}/api/conversations")
async def list_conversations(
    client_code: str,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
    limit: int = 50,
):
    client_uuid = get_or_create_client_uuid(db, client_code)
    conversations = (
        db.query(models.Conversation)
        .filter(models.Conversation.client_id == client_uuid)
        .order_by(models.Conversation.last_message_at.desc().nullslast())
        .limit(min(max(limit, 1), 100))
        .all()
    )
    result = []
    for conversation in conversations:
        contact = db.query(models.Contact).filter(
            models.Contact.client_id == client_uuid,
            models.Contact.id == conversation.contact_id,
        ).first()
        last_message = (
            db.query(models.Message)
            .filter(
                models.Message.client_id == client_uuid,
                models.Message.conversation_id == conversation.id,
            )
            .order_by(models.Message.created_at.desc())
            .first()
        )
        open_handover = (
            db.query(models.HandoverEvent)
            .filter(
                models.HandoverEvent.client_id == client_uuid,
                models.HandoverEvent.conversation_id == conversation.id,
                models.HandoverEvent.status == "open",
            )
            .order_by(models.HandoverEvent.triggered_at.desc())
            .first()
        )
        result.append(
            {
                "id": str(conversation.id),
                "phone": contact.phone_e164 if contact else "",
                "name": contact.display_name if contact else "",
                "status": conversation.status,
                "bot_enabled": conversation.bot_enabled,
                "last_message_at": _iso(conversation.last_message_at),
                "last_direction": last_message.direction if last_message else "",
                "last_text": (last_message.text_content or "") if last_message else "",
                "last_send_success": (last_message.meta_data or {}).get("send_success") if last_message else None,
                "open_handover_reason": open_handover.reason if open_handover else "",
            }
        )
    return {"conversations": result}


@router.get("/admin/{client_code}/api/knowledge")
async def list_knowledge(
    client_code: str,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    client_uuid = get_or_create_client_uuid(db, client_code)
    return {"documents": list_knowledge_documents(db, client_uuid)}


@router.post("/admin/{client_code}/api/knowledge/text")
async def ingest_knowledge_text(
    client_code: str,
    body: KnowledgeTextRequest,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    client_uuid = get_or_create_client_uuid(db, client_code)
    try:
        return ingest_text_knowledge(
            db,
            client_id=client_uuid,
            client_code=client_code,
            title=body.title,
            text=body.text,
            source_type=body.source_type,
            document_type=body.document_type,
            doc_priority=body.doc_priority,
            metadata=body.metadata,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/admin/{client_code}/api/conversations/{conversation_id}/messages")
async def conversation_messages(
    client_code: str,
    conversation_id: uuid.UUID,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
    limit: int = 80,
):
    client_uuid = get_or_create_client_uuid(db, client_code)
    conversation = _conversation_or_404(db, client_uuid, conversation_id)
    contact = db.query(models.Contact).filter(
        models.Contact.client_id == client_uuid,
        models.Contact.id == conversation.contact_id,
    ).first()
    rows = (
        db.query(models.Message)
        .filter(models.Message.client_id == client_uuid, models.Message.conversation_id == conversation.id)
        .order_by(models.Message.created_at.desc())
        .limit(min(max(limit, 1), 200))
        .all()
    )
    handovers = (
        db.query(models.HandoverEvent)
        .filter(models.HandoverEvent.client_id == client_uuid, models.HandoverEvent.conversation_id == conversation.id)
        .order_by(models.HandoverEvent.triggered_at.desc())
        .limit(20)
        .all()
    )
    audits = (
        db.query(models.AuditLog)
        .filter(models.AuditLog.client_id == client_uuid, models.AuditLog.entity_id == conversation.id)
        .order_by(models.AuditLog.created_at.desc())
        .limit(20)
        .all()
    )
    rag_traces = (
        db.query(models.RagSourceTrace)
        .filter(models.RagSourceTrace.client_id == client_uuid, models.RagSourceTrace.conversation_id == conversation.id)
        .order_by(models.RagSourceTrace.created_at.desc())
        .limit(20)
        .all()
    )
    conflicts = (
        db.query(models.KnowledgeConflictLog)
        .filter(models.KnowledgeConflictLog.client_id == client_uuid, models.KnowledgeConflictLog.conversation_id == conversation.id)
        .order_by(models.KnowledgeConflictLog.created_at.desc())
        .limit(20)
        .all()
    )
    messages = []
    for message in reversed(rows):
        raw_payload = None
        if message.raw_payload_id:
            raw = (
                db.query(models.RawWebhookPayload)
                .filter(models.RawWebhookPayload.client_id == client_uuid, models.RawWebhookPayload.id == message.raw_payload_id)
                .first()
            )
            raw_payload = _redact_payload(raw.payload) if raw else None
        messages.append({
            "id": str(message.id),
            "created_at": _iso(message.created_at),
            "direction": message.direction,
            "sender_type": message.sender_type,
            "message_type": message.message_type,
            "text": message.text_content or "",
            "file_url": message.file_url or "",
            "provider_message_id": message.provider_message_id or "",
            "send_success": (message.meta_data or {}).get("send_success"),
            "error": (message.meta_data or {}).get("error", ""),
            "raw_payload": raw_payload,
        })
    return {
        "conversation": {
            "id": str(conversation.id),
            "status": conversation.status,
            "bot_enabled": conversation.bot_enabled,
            "phone": contact.phone_e164 if contact else "",
            "name": contact.display_name if contact else "",
            "last_message_at": _iso(conversation.last_message_at),
        },
        "messages": messages,
        "handovers": [
            {
                "created_at": _iso(event.triggered_at),
                "reason": event.reason,
                "status": event.status,
                "summary": event.summary or "",
            }
            for event in handovers
        ],
        "audits": [
            {
                "created_at": _iso(audit.created_at),
                "event_type": audit.event_type,
                "actor_type": audit.actor_type,
                "new_value": audit.new_value,
            }
            for audit in audits
        ],
        "rag_traces": [
            {
                "created_at": _iso(trace.created_at),
                "answer_type": trace.answer_type,
                "normalized_query": trace.normalized_query,
                "confidence": float(trace.confidence),
                "knowledge_version": trace.knowledge_version,
                "sources": trace.sources,
                "metadata": trace.meta_data,
            }
            for trace in rag_traces
        ],
        "knowledge_conflicts": [
            {
                "created_at": _iso(conflict.created_at),
                "query_text": conflict.query_text,
                "conflict_type": conflict.conflict_type,
                "conflict_fields": conflict.conflict_fields,
                "resolution": conflict.resolution,
                "sources": conflict.sources,
            }
            for conflict in conflicts
        ],
    }


@router.post("/admin/{client_code}/api/conversations/{conversation_id}/bot-on")
async def dashboard_bot_on(
    client_code: str,
    conversation_id: uuid.UUID,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    client_uuid = get_or_create_client_uuid(db, client_code)
    success, message = execute_bot_on(db, client_uuid, conversation_id, "Dashboard bot on")
    _close_open_handovers(db, client_uuid, conversation_id)
    return {"success": success, "message": message}


@router.post("/admin/{client_code}/api/conversations/{conversation_id}/takeover")
async def dashboard_takeover(
    client_code: str,
    conversation_id: uuid.UUID,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    client_uuid = get_or_create_client_uuid(db, client_code)
    success, message = execute_takeover(db, client_uuid, conversation_id)
    return {"success": success, "message": message}


def _conversation_or_404(db: Session, client_uuid: uuid.UUID, conversation_id: uuid.UUID) -> models.Conversation:
    conversation = (
        db.query(models.Conversation)
        .filter(models.Conversation.client_id == client_uuid, models.Conversation.id == conversation_id)
        .first()
    )
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found.")
    return conversation


def _close_open_handovers(db: Session, client_uuid: uuid.UUID, conversation_id: uuid.UUID) -> None:
    events = (
        db.query(models.HandoverEvent)
        .filter(
            models.HandoverEvent.client_id == client_uuid,
            models.HandoverEvent.conversation_id == conversation_id,
            models.HandoverEvent.status == "open",
        )
        .all()
    )
    for event in events:
        event.status = "resolved"
    db.commit()


def _iso(value) -> str:
    return value.isoformat() if value else ""


def _ensure_client_exists(client_code: str) -> None:
    if not client_config_manager.client_exists(client_code):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Tenant '{client_code}' is not configured.")


def _admin_token_for_client(client_code: str) -> str:
    if client_code:
        env_name = "ADMIN_DASHBOARD_TOKEN_" + "".join(ch if ch.isalnum() else "_" for ch in client_code.upper())
        tenant_token = os.getenv(env_name, "")
        if tenant_token:
            return tenant_token
    return os.getenv("ADMIN_DASHBOARD_TOKEN", "")


def _redact_payload(value: Any) -> Any:
    sensitive = {"apikey", "api_key", "authorization", "token", "access_token", "password", "secret"}
    if isinstance(value, dict):
        return {
            key: "[redacted]" if key.lower() in sensitive else _redact_payload(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_payload(item) for item in value[:50]]
    return value


def _dashboard_html(client_code: str) -> str:
    return f"""<!doctype html>
<html lang="id">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>HalloTravel Admin</title>
  <style>
    :root {{
      color-scheme: light;
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
    header {{ height: 56px; display: flex; align-items: center; justify-content: space-between; padding: 0 18px; border-bottom: 1px solid var(--line); background: var(--panel); }}
    h1 {{ font-size: 18px; margin: 0; letter-spacing: 0; }}
    button {{ border: 1px solid var(--line); background: #fff; color: var(--text); height: 34px; padding: 0 12px; border-radius: 6px; cursor: pointer; font-weight: 600; }}
    button.primary {{ background: var(--green); color: #fff; border-color: var(--green); }}
    button.danger {{ background: var(--red); color: #fff; border-color: var(--red); }}
    button:disabled {{ opacity: .5; cursor: not-allowed; }}
    input, select {{ height: 34px; border: 1px solid var(--line); border-radius: 6px; padding: 0 10px; min-width: 180px; background: #fff; }}
    textarea {{ width: 100%; min-height: 92px; border: 1px solid var(--line); border-radius: 6px; padding: 10px; resize: vertical; font: inherit; }}
    .layout {{ display: grid; grid-template-columns: 380px 1fr; height: calc(100vh - 56px); }}
    .sidebar {{ border-right: 1px solid var(--line); background: var(--panel); overflow: auto; }}
    .content {{ min-width: 0; display: grid; grid-template-rows: auto 1fr auto; overflow: hidden; }}
    .toolbar {{ display: flex; gap: 8px; align-items: center; padding: 12px; border-bottom: 1px solid var(--line); background: var(--panel); }}
    .list-item {{ width: 100%; text-align: left; border: 0; border-bottom: 1px solid var(--line); border-radius: 0; height: auto; padding: 12px; display: block; font-weight: 400; }}
    .list-item.active {{ background: #eef4ff; }}
    .row {{ display: flex; align-items: center; justify-content: space-between; gap: 8px; }}
    .name {{ font-weight: 700; overflow-wrap: anywhere; }}
    .phone, .preview, .time {{ color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }}
    .preview {{ margin-top: 6px; line-height: 1.35; }}
    .badge {{ display: inline-flex; align-items: center; min-height: 22px; padding: 0 8px; border-radius: 999px; font-size: 12px; font-weight: 700; border: 1px solid var(--line); background: #fff; white-space: nowrap; }}
    .badge.on {{ color: var(--green); border-color: #9fd3b8; background: #eefaf3; }}
    .badge.off {{ color: var(--red); border-color: #ebb0ac; background: #fff1f0; }}
    .badge.warn {{ color: var(--amber); border-color: #e5c985; background: #fff8df; }}
    .messages {{ padding: 14px; overflow: auto; }}
    .bubble {{ max-width: 760px; margin: 0 0 10px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: #fff; white-space: pre-wrap; line-height: 1.4; overflow-wrap: anywhere; }}
    .bubble.incoming {{ margin-right: auto; }}
    .bubble.outgoing {{ margin-left: auto; background: #eefaf3; border-color: #b7dec7; }}
    .meta {{ color: var(--muted); font-size: 12px; margin-bottom: 5px; display: flex; justify-content: space-between; gap: 10px; }}
    .bottom {{ border-top: 1px solid var(--line); background: var(--panel); padding: 10px 12px; max-height: 320px; overflow: auto; }}
    .stack {{ display: grid; gap: 8px; }}
    .knowledge-grid {{ display: grid; grid-template-columns: 1fr 150px 150px auto; gap: 8px; align-items: start; margin: 10px 0; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 12px; }}
    th, td {{ text-align: left; border-bottom: 1px solid var(--line); padding: 7px; vertical-align: top; }}
    .empty {{ color: var(--muted); padding: 18px; }}
    @media (max-width: 860px) {{
      .layout {{ grid-template-columns: 1fr; grid-template-rows: 42vh 1fr; }}
      .sidebar {{ border-right: 0; border-bottom: 1px solid var(--line); }}
      input {{ min-width: 160px; width: 100%; }}
      .toolbar {{ flex-wrap: wrap; }}
      .knowledge-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>HalloTravel Admin</h1>
    <div class="row">
      <input id="tokenInput" type="password" placeholder="Admin token">
      <button id="saveToken">Simpan</button>
      <button id="refresh">Refresh</button>
    </div>
  </header>
  <main class="layout">
    <aside class="sidebar" id="conversationList"><div class="empty">Memuat conversation...</div></aside>
    <section class="content">
      <div class="toolbar">
        <span id="selectedTitle" class="name">Pilih conversation</span>
        <span id="selectedStatus" class="badge">-</span>
        <button id="botOn" class="primary" disabled>Bot ON</button>
        <button id="takeover" class="danger" disabled>Admin Takeover</button>
      </div>
      <div class="messages" id="messages"><div class="empty">Log pesan akan tampil di sini.</div></div>
      <div class="bottom">
        <strong>Handover & Audit Log</strong>
        <table><thead><tr><th>Waktu</th><th>Tipe</th><th>Detail</th></tr></thead><tbody id="auditRows"></tbody></table>
        <hr>
        <strong>Knowledge / RAG</strong>
        <div class="knowledge-grid">
          <input id="knowledgeTitle" placeholder="Judul knowledge">
          <select id="knowledgeType">
            <option value="itinerary">Itinerary</option>
            <option value="faq">FAQ</option>
            <option value="package">Paket</option>
            <option value="terms">Syarat</option>
          </select>
          <select id="knowledgeSource">
            <option value="admin_override">Admin Override</option>
            <option value="faq">FAQ</option>
            <option value="package_database">Package DB</option>
            <option value="brochure_pdf">Brochure</option>
          </select>
          <button id="ingestKnowledge" class="primary">Tambah</button>
        </div>
        <textarea id="knowledgeText" placeholder="Masukkan data resmi travel: itinerary, hotel, maskapai, fasilitas, FAQ, atau ketentuan."></textarea>
        <table><thead><tr><th>Dokumen</th><th>Tipe</th><th>Chunk</th><th>Versi</th></tr></thead><tbody id="knowledgeRows"></tbody></table>
      </div>
    </section>
  </main>
  <script>
    const clientCode = "{client_code}";
    let token = new URLSearchParams(location.search).get("token") || sessionStorage.getItem("adminToken") || "";
    let selectedId = "";
    const tokenInput = document.getElementById("tokenInput");
    tokenInput.value = token;

    function authUrl(path) {{
      return path;
    }}
    async function request(path, options = {{}}) {{
      const headers = Object.assign({{}}, options.headers || {{}});
      if (token) headers["X-Admin-Token"] = token;
      const res = await fetch(authUrl(path), Object.assign({{}}, options, {{headers}}));
      if (!res.ok) throw new Error(`${{res.status}} ${{await res.text()}}`);
      return res.json();
    }}
    function fmtTime(value) {{
      if (!value) return "-";
      try {{ return new Date(value).toLocaleString("id-ID"); }} catch {{ return value; }}
    }}
    function esc(value) {{
      return String(value ?? "").replace(/[&<>"']/g, ch => ({{"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}}[ch]));
    }}
    function statusBadge(conversation) {{
      if (conversation.bot_enabled && conversation.status === "bot_active") return '<span class="badge on">Bot ON</span>';
      if (conversation.status === "handover_required") return '<span class="badge warn">Handover</span>';
      return '<span class="badge off">Bot OFF</span>';
    }}
    async function loadConversations() {{
      const list = document.getElementById("conversationList");
      list.innerHTML = '<div class="empty">Memuat conversation...</div>';
      try {{
        const data = await request(`/admin/${{clientCode}}/api/conversations`);
        if (!data.conversations.length) {{
          list.innerHTML = '<div class="empty">Belum ada conversation.</div>';
          return;
        }}
        list.innerHTML = data.conversations.map(c => `
          <button class="list-item ${{c.id === selectedId ? "active" : ""}}" data-id="${{c.id}}">
            <div class="row"><span class="name">${{esc(c.name || c.phone || "Tanpa nama")}}</span>${{statusBadge(c)}}</div>
            <div class="phone">${{esc(c.phone)}} · ${{fmtTime(c.last_message_at)}}</div>
            <div class="preview">${{esc(c.last_direction)}}: ${{esc((c.last_text || "").slice(0, 130))}}</div>
          </button>
        `).join("");
        list.querySelectorAll(".list-item").forEach(btn => btn.addEventListener("click", () => loadMessages(btn.dataset.id)));
        if (!selectedId && data.conversations[0]) loadMessages(data.conversations[0].id);
      }} catch (err) {{
        list.innerHTML = `<div class="empty">${{esc(err.message)}}</div>`;
      }}
    }}
    async function loadMessages(id) {{
      selectedId = id;
      document.getElementById("botOn").disabled = false;
      document.getElementById("takeover").disabled = false;
      const data = await request(`/admin/${{clientCode}}/api/conversations/${{id}}/messages`);
      const c = data.conversation;
      document.getElementById("selectedTitle").textContent = `${{c.name || "Tanpa nama"}} · ${{c.phone}}`;
      document.getElementById("selectedStatus").textContent = `${{c.status}} · bot=${{c.bot_enabled ? "on" : "off"}}`;
      document.getElementById("selectedStatus").className = `badge ${{c.bot_enabled ? "on" : "off"}}`;
      document.getElementById("messages").innerHTML = data.messages.map(m => `
        <div class="bubble ${{m.direction}}">
          <div class="meta"><span>${{esc(m.direction)}} · ${{esc(m.message_type)}} · send=${{m.send_success}}</span><span>${{fmtTime(m.created_at)}}</span></div>
          ${{esc(m.text || m.file_url || "-")}}
          ${{m.error ? `<div class="preview">Error: ${{esc(m.error)}}</div>` : ""}}
          ${{m.raw_payload ? `<details><summary class="preview">Raw webhook</summary><pre>${{esc(JSON.stringify(m.raw_payload, null, 2))}}</pre></details>` : ""}}
        </div>
      `).join("") || '<div class="empty">Belum ada pesan.</div>';
      document.getElementById("auditRows").innerHTML = [
        ...data.handovers.map(h => ({{time: h.created_at, type: `handover:${{h.status}}`, detail: `${{h.reason}} - ${{h.summary}}`}})),
        ...data.audits.map(a => ({{time: a.created_at, type: a.event_type, detail: JSON.stringify(a.new_value || {{}})}})),
        ...data.rag_traces.map(r => ({{time: r.created_at, type: `rag:${{r.confidence}}`, detail: `${{r.normalized_query}} | ${{JSON.stringify(r.sources || [])}}`}})),
        ...data.knowledge_conflicts.map(k => ({{time: k.created_at, type: `conflict:${{k.conflict_type}}`, detail: `${{k.query_text}} | ${{JSON.stringify(k.conflict_fields || [])}}`}})),
      ].sort((a, b) => String(b.time).localeCompare(String(a.time))).map(row => `
        <tr><td>${{fmtTime(row.time)}}</td><td>${{esc(row.type)}}</td><td>${{esc(row.detail)}}</td></tr>
      `).join("");
      await loadConversations();
    }}

    async function loadKnowledge() {{
      try {{
        const data = await request(`/admin/${{clientCode}}/api/knowledge`);
        document.getElementById("knowledgeRows").innerHTML = data.documents.map(d => `
          <tr>
            <td>${{esc(d.title)}}</td>
            <td>${{esc(d.document_type || d.source_type)}}</td>
            <td>${{d.chunk_count}}</td>
            <td>${{esc(d.doc_version)}}</td>
          </tr>
        `).join("") || '<tr><td colspan="4">Belum ada knowledge.</td></tr>';
      }} catch (err) {{
        document.getElementById("knowledgeRows").innerHTML = `<tr><td colspan="4">${{esc(err.message)}}</td></tr>`;
      }}
    }}

    async function ingestKnowledge() {{
      const title = document.getElementById("knowledgeTitle").value.trim();
      const text = document.getElementById("knowledgeText").value.trim();
      if (!title || text.length < 20) {{
        alert("Judul dan isi knowledge minimal 20 karakter wajib diisi.");
        return;
      }}
      await request(`/admin/${{clientCode}}/api/knowledge/text`, {{
        method: "POST",
        headers: {{"Content-Type": "application/json"}},
        body: JSON.stringify({{
          title,
          text,
          document_type: document.getElementById("knowledgeType").value,
          source_type: document.getElementById("knowledgeSource").value,
          doc_priority: 80,
        }}),
      }});
      document.getElementById("knowledgeTitle").value = "";
      document.getElementById("knowledgeText").value = "";
      await loadKnowledge();
      alert("Knowledge berhasil ditambahkan.");
    }}

    async function action(path) {{
      if (!selectedId) return;
      await request(`/admin/${{clientCode}}/api/conversations/${{selectedId}}/${{path}}`, {{method: "POST"}});
      await loadMessages(selectedId);
    }}
    document.getElementById("saveToken").addEventListener("click", () => {{
      token = tokenInput.value.trim();
      sessionStorage.setItem("adminToken", token);
      loadConversations();
    }});
    document.getElementById("refresh").addEventListener("click", loadConversations);
    document.getElementById("botOn").addEventListener("click", () => action("bot-on"));
    document.getElementById("takeover").addEventListener("click", () => action("takeover"));
    document.getElementById("ingestKnowledge").addEventListener("click", ingestKnowledge);
    loadConversations();
    loadKnowledge();
    setInterval(() => selectedId ? loadMessages(selectedId) : loadConversations(), 15000);
  </script>
</body>
</html>"""
