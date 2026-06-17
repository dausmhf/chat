import json
import shutil
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import client_config_manager
from app.ingestion.event_handler import get_or_create_client_uuid
from app.storage import models


def create_tenant(
    db: Session,
    *,
    client_code: str,
    brand_name: str,
    bot_name: str,
    admin_phone: Optional[str] = None,
    starsender_api_key_env: str = "STARSENDER_API_KEY",
    gemini_api_key_env: str = "GEMINI_API_KEY",
) -> Dict[str, str]:
    if not client_config_manager.is_valid_client_id(client_code):
        raise ValueError("client_code hanya boleh huruf kecil, angka, underscore, dan dash.")
    if client_config_manager.client_exists(client_code):
        raise ValueError(f"Tenant '{client_code}' sudah ada.")

    tenant_dir = client_config_manager.get_writable_client_dir(client_code)
    config_dir = tenant_dir / "config"
    knowledge_dir = tenant_dir / "knowledge"
    config_dir.mkdir(parents=True, exist_ok=False)
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    _write_json(config_dir / "client_config.json", {
        "client_id": client_code,
        "status": "active",
        "brand_name": brand_name,
        "bot_name": bot_name,
        "default_user_call": "Ayah/Bunda",
        "tone": "ramah, sopan, jelas, tidak memaksa",
        "timezone": "Asia/Jakarta",
        "default_language": "id",
        "bot_enabled_default": True,
        "max_memory_turns": 5,
        "admin_notification_phone": admin_phone or "",
    })
    _write_json(config_dir / "channel_config.json", {
        "active_channel": "starsender",
        "channels": {
            "starsender": {
                "enabled": True,
                "api_key_env": starsender_api_key_env,
                "webhook_secret_env": f"STARSENDER_WEBHOOK_SECRET_{_env_suffix(client_code)}",
            },
            "waba": {
                "enabled": False,
                "provider": "meta_cloud_api",
                "access_token_env": f"WABA_ACCESS_TOKEN_{_env_suffix(client_code)}",
                "phone_number_id_env": f"WABA_PHONE_NUMBER_ID_{_env_suffix(client_code)}",
                "verify_token_env": f"WABA_VERIFY_TOKEN_{_env_suffix(client_code)}",
            },
        },
    })
    _write_json(config_dir / "payment_config.json", {
        "bank_accounts": [],
        "payment_instruction": "Transfer sesuai nominal invoice, lalu kirim bukti transfer untuk dicek admin.",
        "verification_message": "Pembayaran akan diverifikasi manual oleh admin melalui mutasi rekening resmi.",
    })
    _write_json(config_dir / "ai_config.json", {
        "provider": "gemini",
        "api_key_env": gemini_api_key_env,
        "chat_model": "gemini-2.5-flash",
        "embedding_model": "gemini-embedding-001",
        "embedding_dimensions": 1536,
        "thinking_budget": 0,
        "temperature": 0.25,
        "max_output_tokens": 650,
        "timeout_seconds": 25,
        "safety": {
            "intent_confidence_min": 0.70,
            "retrieval_score_min": 0.55,
            "retrieval_score_confident": 0.75,
        },
        "token_optimization": {
            "enabled": True,
            "rag_top_k": 3,
            "max_chunk_tokens": 500,
            "response_cache_enabled": True,
            "response_cache_ttl_minutes": 60,
        },
    })
    _write_json(config_dir / "feature_flags.json", {
        "rag_answering": True,
        "booking_flow": True,
        "invoice_pdf": True,
        "payment_evidence_triage": True,
        "admin_takeover": True,
    })
    (knowledge_dir / "brand_voice.md").write_text(
        f"# Brand Voice - {brand_name}\n\n"
        "- Ramah\n- Sopan\n- Jelas\n- Tidak memaksa\n\n"
        "Panggil user dengan Ayah/Bunda kecuali user memberi nama sendiri.\n",
        encoding="utf-8",
    )

    client_uuid = get_or_create_client_uuid(db, client_code)
    db.add(models.AuditLog(
        client_id=client_uuid,
        actor_type="admin",
        event_type="tenant_created",
        entity_type="clients",
        entity_id=client_uuid,
        new_value={"client_code": client_code, "brand_name": brand_name},
    ))
    db.commit()
    return {
        "client_code": client_code,
        "brand_name": brand_name,
        "status": "active",
        "dashboard_url": f"/admin/{client_code}/dashboard",
        "webhook_url": f"/webhooks/starsender/{client_code}",
    }


def list_tenants(db: Session) -> List[Dict[str, str]]:
    tenants = []
    for client_code in client_config_manager.list_client_ids():
        configs = client_config_manager.load_all_configs(client_code)
        client_config = configs["client"]
        channel_config = configs["channel"]
        ai_config = configs["ai"]
        active_channel = channel_config.get("active_channel", "starsender")
        channel_settings = channel_config.get("channels", {}).get(active_channel, {})
        client = db.query(models.Client).filter(models.Client.client_code == client_code).first()
        status = _tenant_status(client_config)
        if client and client.status != status:
            client.status = status
            db.commit()
        tenants.append({
            "client_code": client_code,
            "brand_name": client_config.get("brand_name", client_code),
            "bot_name": client_config.get("bot_name", ""),
            "status": status,
            "admin_notification_phone": client_config.get("admin_notification_phone") or client_config.get("admin_group_id") or "",
            "active_channel": active_channel,
            "channel_enabled": bool(channel_settings.get("enabled", False)),
            "starsender_api_key_env": channel_settings.get("api_key_env", "STARSENDER_API_KEY"),
            "starsender_webhook_secret_env": channel_settings.get("webhook_secret_env", "STARSENDER_WEBHOOK_SECRET"),
            "gemini_api_key_env": ai_config.get("api_key_env", "GEMINI_API_KEY"),
            "dashboard_url": f"/admin/{client_code}/dashboard",
            "webhook_url": f"/webhooks/starsender/{client_code}",
        })
    return tenants


def update_tenant(
    db: Session,
    *,
    client_code: str,
    brand_name: Optional[str] = None,
    bot_name: Optional[str] = None,
    admin_notification_phone: Optional[str] = None,
    starsender_api_key_env: Optional[str] = None,
    starsender_webhook_secret_env: Optional[str] = None,
    gemini_api_key_env: Optional[str] = None,
    status: Optional[str] = None,
) -> Dict[str, str]:
    if not client_config_manager.client_exists(client_code):
        raise ValueError(f"Tenant '{client_code}' tidak ditemukan.")
    writable_config = _ensure_writable_config(client_code)

    client_config_path = writable_config / "client_config.json"
    channel_config_path = writable_config / "channel_config.json"
    ai_config_path = writable_config / "ai_config.json"

    client_config = _read_json(client_config_path)
    channel_config = _read_json(channel_config_path)
    ai_config = _read_json(ai_config_path)

    if brand_name is not None:
        client_config["brand_name"] = brand_name.strip()
    if bot_name is not None:
        client_config["bot_name"] = bot_name.strip()
    if admin_notification_phone is not None:
        client_config["admin_notification_phone"] = admin_notification_phone.strip()
    if status is not None:
        client_config["status"] = _normalize_status(status)
    if gemini_api_key_env is not None:
        ai_config["api_key_env"] = gemini_api_key_env.strip()
    if starsender_api_key_env is not None or starsender_webhook_secret_env is not None:
        starsender = channel_config.setdefault("channels", {}).setdefault("starsender", {})
        if starsender_api_key_env is not None:
            starsender["api_key_env"] = starsender_api_key_env.strip()
        if starsender_webhook_secret_env is not None:
            starsender["webhook_secret_env"] = starsender_webhook_secret_env.strip()

    _write_json(client_config_path, client_config)
    _write_json(channel_config_path, channel_config)
    _write_json(ai_config_path, ai_config)
    _sync_client_row(db, client_code, client_config)
    _audit_tenant_update(db, client_code, "tenant_updated", {
        "brand_name": client_config.get("brand_name"),
        "bot_name": client_config.get("bot_name"),
        "status": _tenant_status(client_config),
    })
    return get_tenant(db, client_code)


def set_tenant_status(db: Session, client_code: str, status: str) -> Dict[str, str]:
    normalized = _normalize_status(status)
    return update_tenant(db, client_code=client_code, status=normalized)


def get_tenant(db: Session, client_code: str) -> Dict[str, str]:
    for tenant in list_tenants(db):
        if tenant["client_code"] == client_code:
            return tenant
    raise ValueError(f"Tenant '{client_code}' tidak ditemukan.")


def is_tenant_active(client_code: str) -> bool:
    if not client_config_manager.client_exists(client_code):
        return False
    client_config = client_config_manager.load_json_config(client_code, "client_config.json")
    return _tenant_status(client_config) == "active"


def _ensure_writable_config(client_code: str) -> Path:
    source_config = client_config_manager.get_client_dir(client_code) / "config"
    target_config = client_config_manager.get_writable_client_dir(client_code) / "config"
    if source_config.resolve() != target_config.resolve() and not target_config.exists():
        target_config.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_config, target_config)
    target_config.mkdir(parents=True, exist_ok=True)
    return target_config


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _sync_client_row(db: Session, client_code: str, client_config: dict) -> None:
    client = db.query(models.Client).filter(models.Client.client_code == client_code).first()
    if not client:
        client = models.Client(client_code=client_code, name=client_code, brand_name=client_code)
        db.add(client)
    client.name = client_config.get("client_name") or client_config.get("brand_name") or client_code
    client.brand_name = client_config.get("brand_name") or client_config.get("client_name") or client_code
    client.timezone = client_config.get("timezone", client.timezone or "Asia/Jakarta")
    client.status = _tenant_status(client_config)
    db.commit()


def _audit_tenant_update(db: Session, client_code: str, event_type: str, new_value: dict) -> None:
    client_uuid = get_or_create_client_uuid(db, client_code)
    db.add(models.AuditLog(
        client_id=client_uuid,
        actor_type="admin",
        event_type=event_type,
        entity_type="clients",
        entity_id=client_uuid,
        new_value=new_value,
    ))
    db.commit()


def _tenant_status(client_config: dict) -> str:
    return _normalize_status(client_config.get("status", "active"))


def _normalize_status(value: str) -> str:
    normalized = (value or "active").strip().lower()
    if normalized in {"active", "enabled", "on"}:
        return "active"
    if normalized in {"inactive", "disabled", "off", "paused"}:
        return "inactive"
    raise ValueError("Status tenant harus 'active' atau 'inactive'.")


def _env_suffix(client_code: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in client_code.upper())
