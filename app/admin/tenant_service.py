import json
from pathlib import Path
from typing import Dict, Optional

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
        "dashboard_url": f"/admin/{client_code}/dashboard",
        "webhook_url": f"/webhooks/starsender/{client_code}",
    }


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _env_suffix(client_code: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in client_code.upper())
