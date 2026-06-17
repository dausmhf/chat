import os
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.admin.admin_command_service import execute_bot_on, execute_takeover
from app.admin.tenant_service import (
    create_tenant,
    delete_tenant,
    get_tenant_detail,
    list_tenants,
    set_tenant_status,
    update_tenant,
    update_tenant_config_section,
)
from app.config import client_config_manager, settings
from app.ingestion.event_handler import get_or_create_client_uuid
from app.rag.ingestion_service import ingest_file_knowledge, ingest_text_knowledge, list_knowledge_documents
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


class TenantUpdateRequest(BaseModel):
    brand_name: Optional[str] = Field(default=None, min_length=2, max_length=180)
    bot_name: Optional[str] = Field(default=None, min_length=2, max_length=180)
    admin_notification_phone: Optional[str] = None
    starsender_api_key_env: Optional[str] = None
    starsender_webhook_secret_env: Optional[str] = None
    gemini_api_key_env: Optional[str] = None
    status: Optional[str] = None


class ConfigSectionUpdateRequest(BaseModel):
    config: Dict[str, Any]


class ApiKeysUpdateRequest(BaseModel):
    keys: Dict[str, str]


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


# ===========================================================================
# Tenant CRUD Endpoints
# ===========================================================================

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


@router.get("/admin/api/tenants")
async def list_tenants_endpoint(
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    return {"tenants": list_tenants(db)}


@router.get("/admin/api/tenants/{client_code}/detail")
async def get_tenant_detail_endpoint(
    client_code: str,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    try:
        return get_tenant_detail(db, client_code)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/admin/api/tenants/{client_code}")
async def update_tenant_endpoint(
    client_code: str,
    body: TenantUpdateRequest,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    try:
        return update_tenant(db, client_code=client_code, **body.dict(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/admin/api/tenants/{client_code}/config/{section}")
async def update_config_section_endpoint(
    client_code: str,
    section: str,
    body: ConfigSectionUpdateRequest,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    try:
        return update_tenant_config_section(
            db, client_code=client_code, section=section, config_data=body.config
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/admin/api/tenants/{client_code}/api-keys")
async def update_api_keys_endpoint(
    client_code: str,
    body: ApiKeysUpdateRequest,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    from app.admin.secrets_manager import set_secrets_bulk
    try:
        result = set_secrets_bulk(client_code, body.keys)
        return {"api_keys": result}
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/admin/api/tenants/{client_code}/enable")
async def enable_tenant_endpoint(
    client_code: str,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    try:
        return set_tenant_status(db, client_code, "active")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.post("/admin/api/tenants/{client_code}/disable")
async def disable_tenant_endpoint(
    client_code: str,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    try:
        return set_tenant_status(db, client_code, "inactive")
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/admin/api/tenants/{client_code}")
async def delete_tenant_endpoint(
    client_code: str,
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    try:
        return delete_tenant(db, client_code)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# ===========================================================================
# Conversation & Knowledge Endpoints (unchanged)
# ===========================================================================

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


@router.post("/admin/{client_code}/api/knowledge/file")
async def ingest_knowledge_file(
    client_code: str,
    title: str = Form(...),
    source_type: str = Form("brochure_pdf"),
    document_type: str = Form("faq"),
    doc_priority: int = Form(70),
    file: UploadFile = File(...),
    _: None = Depends(require_admin_token),
    db: Session = Depends(get_db),
):
    client_uuid = get_or_create_client_uuid(db, client_code)
    content = await file.read()
    try:
        return ingest_file_knowledge(
            db,
            client_id=client_uuid,
            client_code=client_code,
            title=title,
            filename=file.filename or "knowledge.txt",
            content=content,
            content_type=file.content_type or "",
            source_type=source_type,
            document_type=document_type,
            doc_priority=doc_priority,
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


# ===========================================================================
# Helpers
# ===========================================================================

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
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>HalloTravel · {client_code}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0}}
:root{{
  --bg:#f0f2f5;--white:#ffffff;
  --border:#e5e9f0;--border-light:#f0f2f5;
  --text:#1a1d26;--text2:#5a6178;--muted:#8b92a8;
  --primary:#2563eb;--primary-light:#3b82f6;--primary-bg:#eff6ff;--primary-border:#bfdbfe;
  --green:#10b981;--green-bg:#ecfdf5;--green-border:#a7f3d0;
  --red:#ef4444;--red-bg:#fef2f2;--red-border:#fecaca;
  --amber:#f59e0b;--amber-bg:#fffbeb;
  --purple:#8b5cf6;--purple-bg:#f5f3ff;
  --radius:12px;--radius-lg:16px;
  --shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --shadow-md:0 4px 12px rgba(0,0,0,.08);
  --transition:all .2s ease;
}}
html{{font-size:14px}}
body{{font-family:'Inter',system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh;overflow:hidden}}

/* Toast */
#toast{{position:fixed;top:20px;right:20px;z-index:9999;display:flex;flex-direction:column;gap:8px}}
.toast-item{{padding:12px 20px;border-radius:var(--radius);font-size:13px;font-weight:600;color:#fff;box-shadow:var(--shadow-md);animation:toastIn .3s ease,toastOut .4s ease 2.6s forwards}}
.toast-item.success{{background:var(--green)}}.toast-item.error{{background:var(--red)}}.toast-item.info{{background:var(--primary)}}
@keyframes toastIn{{from{{opacity:0;transform:translateY(-12px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes toastOut{{from{{opacity:1}}to{{opacity:0}}}}

/* Layout */
.app{{display:grid;grid-template-columns:220px 360px 1fr;height:100vh}}
.app.view-knowledge{{grid-template-columns:220px 1fr}}
.app.view-knowledge .conv-panel{{display:none}}
.app.view-knowledge .messages-area{{display:none}}
.app.view-knowledge .bottom-area{{flex:1;max-height:none}}

/* Sidebar */
.sidebar{{background:var(--white);border-right:1px solid var(--border);display:flex;flex-direction:column}}
.sidebar-brand{{padding:20px 16px;display:flex;align-items:center;gap:10px;border-bottom:1px solid var(--border)}}
.sidebar-brand .logo-icon{{width:32px;height:32px;background:var(--primary);border-radius:8px;display:flex;align-items:center;justify-content:center;color:#fff;font-weight:800;font-size:14px}}
.sidebar-brand h1{{font-size:14px;font-weight:700}}
.sidebar-nav{{padding:12px;display:flex;flex-direction:column;gap:2px;flex:1}}
.nav-item{{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;font-size:13px;font-weight:500;color:var(--text2);cursor:pointer;transition:var(--transition);border:none;background:transparent;width:100%;text-align:left}}
.nav-item:hover{{background:var(--bg)}}
.nav-item.active{{background:var(--primary);color:#fff;font-weight:600;box-shadow:0 2px 8px rgba(37,99,235,.25)}}
.nav-item .icon{{font-size:16px;width:20px;text-align:center}}
.nav-item .nav-badge{{margin-left:auto;background:var(--red);color:#fff;font-size:10px;font-weight:700;padding:2px 7px;border-radius:10px}}
.sidebar-footer{{padding:12px 16px;border-top:1px solid var(--border)}}
.sidebar-footer .token-row{{display:flex;gap:6px}}
.sidebar-footer input{{flex:1;height:34px;font-size:12px}}
.sidebar-footer button{{height:34px;font-size:11px;padding:0 10px}}

/* Middle panel - conversations */
.conv-panel{{background:var(--white);border-right:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}}
.conv-header{{padding:16px;border-bottom:1px solid var(--border);display:flex;flex-direction:column;gap:10px}}
.conv-header h2{{font-size:15px;font-weight:700}}
.conv-search input{{width:100%;height:38px;border:1px solid var(--border);border-radius:10px;padding:0 14px;font-size:13px;background:var(--bg);outline:none}}
.conv-search input:focus{{border-color:var(--primary)}}
.conv-list{{flex:1;overflow-y:auto}}
.conv-item{{width:100%;text-align:left;border:none;border-radius:0;border-bottom:1px solid var(--border-light);padding:14px 16px;display:grid;gap:4px;cursor:pointer;transition:var(--transition);background:transparent;height:auto}}
.conv-item:hover{{background:var(--bg)}}
.conv-item.selected{{background:var(--primary-bg);border-left:3px solid var(--primary)}}
.conv-item .c-row{{display:flex;align-items:center;justify-content:space-between;gap:6px;width:100%;overflow:hidden}}
.conv-item .c-name{{font-weight:600;font-size:13px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}}
.conv-item .c-time{{font-size:11px;color:var(--muted);white-space:nowrap;flex-shrink:0}}
.conv-item .c-preview{{font-size:12px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:2px;flex:1;min-width:0}}

/* Badges */
.badge{{display:inline-flex;align-items:center;gap:4px;padding:3px 8px;border-radius:20px;font-size:10px;font-weight:600}}
.badge::before{{content:"";width:5px;height:5px;border-radius:50%}}
.badge.on{{color:var(--green);background:var(--green-bg);border:1px solid var(--green-border)}}.badge.on::before{{background:var(--green)}}
.badge.off{{color:var(--red);background:var(--red-bg);border:1px solid var(--red-border)}}.badge.off::before{{background:var(--red)}}
.badge.warn{{color:var(--amber);background:var(--amber-bg);border:1px solid rgba(245,158,11,.25)}}.badge.warn::before{{background:var(--amber)}}

/* Right panel - content */
.main-panel{{display:flex;flex-direction:column;overflow:hidden}}
.main-topbar{{height:60px;background:var(--white);border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;padding:0 20px;flex-shrink:0}}
.main-topbar-left{{display:flex;align-items:center;gap:12px}}
.main-topbar-left .contact-name{{font-weight:700;font-size:14px}}
.main-topbar-left .contact-phone{{font-size:12px;color:var(--muted)}}
.main-topbar-right{{display:flex;gap:8px}}

/* Buttons */
button{{font-family:inherit;font-size:13px;font-weight:600;border:1px solid var(--border);border-radius:var(--radius);background:var(--white);color:var(--text);height:38px;padding:0 16px;cursor:pointer;transition:var(--transition);display:inline-flex;align-items:center;justify-content:center;gap:6px;white-space:nowrap}}
button:hover{{background:var(--bg)}}
button:active{{transform:scale(.98)}}
button:disabled{{opacity:.4;cursor:not-allowed}}
button.primary{{background:var(--primary);border-color:var(--primary);color:#fff;box-shadow:0 2px 6px rgba(37,99,235,.2)}}
button.primary:hover{{background:var(--primary-light)}}
button.danger{{background:var(--white);border-color:var(--red-border);color:var(--red)}}
button.danger:hover{{background:var(--red-bg)}}
button.sm{{height:32px;font-size:12px;padding:0 12px;border-radius:8px}}
input,select,textarea{{font-family:inherit;font-size:13px;color:var(--text);background:var(--white);border:1px solid var(--border);border-radius:var(--radius);padding:0 12px;height:38px;outline:none;transition:var(--transition)}}
input:focus,textarea:focus{{border-color:var(--primary);box-shadow:0 0 0 3px var(--primary-bg)}}
textarea{{height:auto;min-height:80px;padding:10px 12px;resize:vertical;width:100%}}
input[type=file]{{padding:8px 12px;height:auto}}

/* Messages */
.messages-area{{flex:1;overflow-y:auto;padding:20px;background:var(--bg)}}
.bubble{{max-width:680px;margin-bottom:12px;padding:12px 16px;border:1px solid var(--border);border-radius:var(--radius-lg);background:var(--white);line-height:1.5;overflow-wrap:anywhere;white-space:pre-wrap;box-shadow:var(--shadow)}}
.bubble.incoming{{margin-right:auto;border-bottom-left-radius:4px}}
.bubble.outgoing{{margin-left:auto;background:var(--primary-bg);border-color:var(--primary-border);border-bottom-right-radius:4px}}
.bubble .msg-meta{{display:flex;justify-content:space-between;gap:10px;font-size:11px;color:var(--muted);margin-bottom:6px}}
.bubble .msg-error{{color:var(--red);font-size:11px;margin-top:6px}}

/* Bottom area */
.bottom-area{{border-top:1px solid var(--border);background:var(--white);max-height:350px;overflow-y:auto}}
.bottom-tabs{{display:flex;border-bottom:1px solid var(--border)}}
.btab{{border:none;border-radius:0;background:transparent;color:var(--muted);font-weight:600;padding:12px 18px;font-size:12px;cursor:pointer;border-bottom:2px solid transparent;height:auto}}
.btab:hover{{color:var(--text)}}
.btab.active{{color:var(--primary);border-bottom-color:var(--primary)}}
.btab-panel{{display:none;padding:16px 20px}}
.btab-panel.active{{display:block}}

/* Knowledge */
.knowledge-form{{display:grid;grid-template-columns:1fr 150px 150px auto;gap:8px;align-items:end;margin-bottom:12px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;padding:8px 10px;font-weight:600;color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.5px;border-bottom:2px solid var(--border)}}
td{{padding:8px 10px;border-bottom:1px solid var(--border-light)}}
tr:hover td{{background:var(--bg)}}

/* Cards */
.card{{background:var(--white);border:1px solid var(--border);border-radius:var(--radius-lg);padding:20px;box-shadow:var(--shadow)}}

/* Empty */
.empty{{color:var(--muted);padding:40px;text-align:center;font-size:13px}}

/* Responsive */
@media(max-width:900px){{
  .app{{grid-template-columns:1fr;grid-template-rows:auto 1fr}}
  .sidebar,.conv-panel{{display:none}}
  .knowledge-form{{grid-template-columns:1fr}}
}}
</style>
</head>
<body>
<div class="app">
<!-- Sidebar -->
<aside class="sidebar">
  <div class="sidebar-brand">
    <div class="logo-icon">H</div>
    <h1>{client_code}</h1>
  </div>
  <div class="sidebar-nav">
    <button class="nav-item active" data-view="conversations"><span class="icon">💬</span> Conversations</button>
    <button class="nav-item" data-view="knowledge"><span class="icon">📚</span> Knowledge</button>
    <a href="/admin" style="text-decoration:none"><button class="nav-item" style="width:100%"><span class="icon">🏠</span> Super Admin</button></a>
  </div>
  <div class="sidebar-footer">
    <div class="token-row">
      <input id="tokenInput" type="password" placeholder="Token">
      <button id="saveToken" class="sm primary">OK</button>
    </div>
  </div>
</aside>

<!-- Conversations List -->
<div class="conv-panel">
  <div class="conv-header">
    <div style="display:flex;align-items:center;justify-content:space-between">
      <h2>Conversations</h2>
      <button id="refreshBtn" class="sm" onclick="loadConversations()">↻</button>
    </div>
    <div class="conv-search"><input id="convSearch" type="text" placeholder="Cari nama / nomor..."></div>
  </div>
  <div class="conv-list" id="conversationList"><div class="empty">Memuat...</div></div>
</div>

<!-- Main Content -->
<div class="main-panel">
  <div class="main-topbar">
    <div class="main-topbar-left">
      <div>
        <div class="contact-name" id="selectedName">Pilih conversation</div>
        <div class="contact-phone" id="selectedPhone">-</div>
      </div>
      <span id="selectedBadge" class="badge on" style="display:none">-</span>
    </div>
    <div class="main-topbar-right">
      <button id="botOn" class="primary sm" disabled>✅ Bot ON</button>
      <button id="takeover" class="danger sm" disabled>🛑 Takeover</button>
    </div>
  </div>
  <div class="messages-area" id="messages"><div class="empty">Pilih conversation untuk melihat pesan</div></div>
  <div class="bottom-area">
    <div class="bottom-tabs">
      <button class="btab active" data-panel="logs">📋 Handover & Audit</button>
      <button class="btab" data-panel="knowledge">📚 Knowledge</button>
      <button class="btab" data-panel="addknowledge">➕ Tambah Knowledge</button>
    </div>
    <div class="btab-panel active" id="panel-logs">
      <table><thead><tr><th>Waktu</th><th>Tipe</th><th>Detail</th></tr></thead><tbody id="auditRows"><tr><td colspan="3" class="empty">Pilih conversation</td></tr></tbody></table>
    </div>
    <div class="btab-panel" id="panel-knowledge">
      <table><thead><tr><th>Dokumen</th><th>Tipe</th><th>Chunks</th><th>Versi</th></tr></thead><tbody id="knowledgeRows"><tr><td colspan="4" class="empty">Memuat...</td></tr></tbody></table>
    </div>
    <div class="btab-panel" id="panel-addknowledge">
      <div style="margin-bottom:16px">
        <strong style="font-size:13px">Tambah Knowledge Teks</strong>
        <div class="knowledge-form" style="margin-top:8px">
          <input id="knowledgeTitle" placeholder="Judul knowledge">
          <select id="knowledgeType"><option value="itinerary">Itinerary</option><option value="faq">FAQ</option><option value="package">Paket</option><option value="terms">Syarat</option></select>
          <select id="knowledgeSource"><option value="admin_override">Admin Override</option><option value="faq">FAQ</option><option value="package_database">Package DB</option><option value="brochure_pdf">Brochure</option></select>
          <button id="ingestKnowledge" class="primary sm">Tambah</button>
        </div>
        <textarea id="knowledgeText" placeholder="Masukkan data resmi: itinerary, hotel, maskapai, fasilitas, FAQ, atau ketentuan..." rows="3"></textarea>
      </div>
      <div>
        <strong style="font-size:13px">Upload File Knowledge</strong>
        <div class="knowledge-form" style="margin-top:8px">
          <input id="knowledgeFileTitle" placeholder="Judul file">
          <input id="knowledgeFile" type="file" accept=".pdf,.txt,.md">
          <button id="uploadKnowledgeFile" class="primary sm" style="grid-column:span 2">Upload File</button>
        </div>
      </div>
    </div>
  </div>
</div>
</div>

<div id="toast"></div>

<script>
const clientCode="{client_code}";
let token=new URLSearchParams(location.search).get("token")||sessionStorage.getItem("adminToken")||"";
let selectedId="";
let allConversations=[];
document.getElementById("tokenInput").value=token;

function esc(v){{return String(v??"").replace(/[&<>"']/g,c=>({{"\u0026":"\u0026amp;","<":"\u0026lt;",">":"\u0026gt;",'"':"\u0026quot;","'":"\u0026#39;"}}[c]))}}
function fmtTime(v){{if(!v)return"-";try{{return new Date(v).toLocaleString("id-ID")}}catch{{return v}}}}
function toast(msg,type="info"){{const el=document.createElement("div");el.className="toast-item "+type;el.textContent=msg;document.getElementById("toast").appendChild(el);setTimeout(()=>el.remove(),3100)}}

async function request(path,opts={{}}){{
  const h=Object.assign({{}},opts.headers||{{}});if(token)h["X-Admin-Token"]=token;
  const res=await fetch(path,Object.assign({{}},opts,{{headers:h}}));
  if(!res.ok)throw new Error(`${{res.status}} ${{await res.text()}}`);return res.json();
}}

function statusBadge(c){{
  if(c.bot_enabled&&c.status==="bot_active")return '<span class="badge on">Bot ON</span>';
  if(c.status==="handover_required")return '<span class="badge warn">Handover</span>';
  return '<span class="badge off">Bot OFF</span>';
}}

async function loadConversations(){{
  const list=document.getElementById("conversationList");
  list.innerHTML='<div class="empty">Memuat...</div>';
  try{{
    const data=await request(`/admin/${{clientCode}}/api/conversations`);
    allConversations=data.conversations||[];
    renderConversations();
    if(!selectedId&&allConversations[0])loadMessages(allConversations[0].id);
  }}catch(err){{list.innerHTML=`<div class="empty">${{esc(err.message)}}</div>`}}
}}

function renderConversations(){{
  const list=document.getElementById("conversationList");
  const q=(document.getElementById("convSearch").value||"").toLowerCase();
  const filtered=allConversations.filter(c=>(c.name+c.phone).toLowerCase().includes(q));
  if(!filtered.length){{list.innerHTML='<div class="empty">Tidak ada conversation</div>';return}}
  list.innerHTML=filtered.map(c=>`
    <button class="conv-item ${{c.id===selectedId?"selected":""}}" data-id="${{c.id}}">
      <div class="c-row"><span class="c-name">${{esc(c.name||c.phone||"Tanpa nama")}}</span>${{statusBadge(c)}}</div>
      <div class="c-row"><span class="c-preview">${{esc((c.last_text||"").slice(0,80))}}</span><span class="c-time">${{fmtTime(c.last_message_at)}}</span></div>
    </button>`).join("");
  list.querySelectorAll(".conv-item").forEach(b=>b.addEventListener("click",()=>loadMessages(b.dataset.id)));
}}

async function loadMessages(id){{
  selectedId=id;
  document.getElementById("botOn").disabled=false;
  document.getElementById("takeover").disabled=false;
  const data=await request(`/admin/${{clientCode}}/api/conversations/${{id}}/messages`);
  const c=data.conversation;

  document.getElementById("selectedName").textContent=c.name||"Tanpa nama";
  document.getElementById("selectedPhone").textContent=c.phone||"-";
  const badge=document.getElementById("selectedBadge");
  badge.style.display="inline-flex";
  badge.textContent=c.bot_enabled?"Bot ON":"Bot OFF";
  badge.className="badge "+(c.bot_enabled?"on":"off");

  const msgArea=document.getElementById("messages");
  msgArea.innerHTML=data.messages.map(m=>`
    <div class="bubble ${{m.direction}}">
      <div class="msg-meta"><span>${{esc(m.direction)}} · ${{esc(m.message_type)}}</span><span>${{fmtTime(m.created_at)}}</span></div>
      ${{esc(m.text||m.file_url||"-")}}
      ${{m.error?`<div class="msg-error">⚠ ${{esc(m.error)}}</div>`:""}}
      ${{m.raw_payload?`<details style="margin-top:6px"><summary style="font-size:11px;color:var(--muted);cursor:pointer">Raw payload</summary><pre style="font-size:11px;color:var(--muted);white-space:pre-wrap;margin-top:4px">${{esc(JSON.stringify(m.raw_payload,null,2))}}</pre></details>`:""}}
    </div>`).join("")||'<div class="empty">Belum ada pesan</div>';
  msgArea.scrollTop=msgArea.scrollHeight;

  document.getElementById("auditRows").innerHTML=[
    ...data.handovers.map(h=>({{time:h.created_at,type:`handover:${{h.status}}`,detail:`${{h.reason}} — ${{h.summary}}`}})),
    ...data.audits.map(a=>({{time:a.created_at,type:a.event_type,detail:JSON.stringify(a.new_value||{{}})}})),
    ...data.rag_traces.map(r=>({{time:r.created_at,type:`rag (${{r.confidence.toFixed(2)}})`,detail:`${{r.normalized_query}}`}})),
    ...data.knowledge_conflicts.map(k=>({{time:k.created_at,type:`conflict:${{k.conflict_type}}`,detail:k.query_text}})),
  ].sort((a,b)=>String(b.time).localeCompare(String(a.time))).map(row=>`
    <tr><td style="white-space:nowrap">${{fmtTime(row.time)}}</td><td><span class="badge on" style="font-size:10px">${{esc(row.type)}}</span></td><td style="color:var(--text2)">${{esc(row.detail)}}</td></tr>
  `).join("")||'<tr><td colspan="3" class="empty">Tidak ada log</td></tr>';

  renderConversations();
}}

async function loadKnowledge(){{
  try{{
    const data=await request(`/admin/${{clientCode}}/api/knowledge`);
    document.getElementById("knowledgeRows").innerHTML=data.documents.map(d=>`
      <tr><td style="font-weight:600">${{esc(d.title)}}</td><td>${{esc(d.document_type||d.source_type)}}</td><td>${{d.chunk_count}}</td><td>${{esc(d.doc_version)}}</td></tr>
    `).join("")||'<tr><td colspan="4" class="empty">Belum ada knowledge</td></tr>';
  }}catch(err){{document.getElementById("knowledgeRows").innerHTML=`<tr><td colspan="4" class="empty">${{esc(err.message)}}</td></tr>`}}
}}

async function ingestKnowledge(){{
  const title=document.getElementById("knowledgeTitle").value.trim();
  const text=document.getElementById("knowledgeText").value.trim();
  if(!title||text.length<20){{toast("Judul dan isi minimal 20 karakter","error");return}}
  try{{
    await request(`/admin/${{clientCode}}/api/knowledge/text`,{{method:"POST",headers:{{"Content-Type":"application/json"}},body:JSON.stringify({{title,text,document_type:document.getElementById("knowledgeType").value,source_type:document.getElementById("knowledgeSource").value,doc_priority:80}})}});
    document.getElementById("knowledgeTitle").value="";document.getElementById("knowledgeText").value="";
    toast("Knowledge ditambahkan!","success");await loadKnowledge();
  }}catch(err){{toast(err.message,"error")}}
}}

async function uploadKnowledgeFile(){{
  const title=document.getElementById("knowledgeFileTitle").value.trim();
  const file=document.getElementById("knowledgeFile").files[0];
  if(!title||!file){{toast("Judul dan file wajib diisi","error");return}}
  const form=new FormData();form.append("title",title);form.append("file",file);
  form.append("document_type",document.getElementById("knowledgeType").value);
  form.append("source_type",document.getElementById("knowledgeSource").value);form.append("doc_priority","80");
  try{{
    await request(`/admin/${{clientCode}}/api/knowledge/file`,{{method:"POST",body:form}});
    document.getElementById("knowledgeFileTitle").value="";document.getElementById("knowledgeFile").value="";
    toast("File uploaded!","success");await loadKnowledge();
  }}catch(err){{toast(err.message,"error")}}
}}

async function action(path){{
  if(!selectedId)return;
  try{{
    await request(`/admin/${{clientCode}}/api/conversations/${{selectedId}}/${{path}}`,{{method:"POST"}});
    toast(path==="bot-on"?"Bot dinyalakan":"Admin takeover aktif","success");
    await loadMessages(selectedId);
  }}catch(err){{toast(err.message,"error")}}
}}

/* Tab switching - bottom */
document.querySelectorAll(".btab").forEach(btn=>btn.addEventListener("click",()=>{{
  document.querySelectorAll(".btab").forEach(b=>b.classList.toggle("active",b===btn));
  document.querySelectorAll(".btab-panel").forEach(p=>p.classList.toggle("active",p.id==="panel-"+btn.dataset.panel));
}}));

/* Sidebar nav */
document.querySelectorAll(".nav-item[data-view]").forEach(btn=>btn.addEventListener("click",()=>{{
  document.querySelectorAll(".nav-item[data-view]").forEach(b=>b.classList.toggle("active",b===btn));
  const app=document.querySelector(".app");
  if(btn.dataset.view==="knowledge"){{
    app.classList.add("view-knowledge");
    const btab=document.querySelector('.btab[data-panel="knowledge"]');
    if(btab)btab.click();
  }}else{{
    app.classList.remove("view-knowledge");
    const btab=document.querySelector('.btab[data-panel="logs"]');
    if(btab)btab.click();
  }}
}}));

/* Events */
document.getElementById("saveToken").addEventListener("click",()=>{{token=document.getElementById("tokenInput").value.trim();sessionStorage.setItem("adminToken",token);loadConversations();loadKnowledge()}});
document.getElementById("botOn").addEventListener("click",()=>action("bot-on"));
document.getElementById("takeover").addEventListener("click",()=>action("takeover"));
document.getElementById("ingestKnowledge").addEventListener("click",ingestKnowledge);
document.getElementById("uploadKnowledgeFile").addEventListener("click",uploadKnowledgeFile);
document.getElementById("convSearch").addEventListener("input",renderConversations);

loadConversations();
loadKnowledge();
setInterval(()=>selectedId?loadMessages(selectedId):loadConversations(),15000);
</script>
</body>
</html>"""
