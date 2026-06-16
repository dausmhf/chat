import json

import pytest

from app.channels.factory import build_channel_adapter
from app.config import ClientConfigManager, client_config_manager
from app.ingestion.event_handler import get_or_create_client_uuid
from app.storage import models


def _write_tenant(base, client_id, brand_name="Tenant Travel"):
    config_dir = base / "clients" / client_id / "config"
    config_dir.mkdir(parents=True)
    files = {
        "client_config.json": {
            "client_id": client_id,
            "brand_name": brand_name,
            "timezone": "Asia/Jakarta",
        },
        "channel_config.json": {
            "active_channel": "starsender",
            "channels": {
                "starsender": {
                    "enabled": True,
                    "api_key_env": "TENANT_STARSENDER_KEY",
                    "webhook_secret_env": "TENANT_WEBHOOK_SECRET",
                }
            },
        },
        "payment_config.json": {"bank_accounts": []},
        "ai_config.json": {"provider": "gemini", "api_key_env": "GEMINI_API_KEY"},
        "feature_flags.json": {"rag_answering": True},
    }
    for filename, payload in files.items():
        (config_dir / filename).write_text(json.dumps(payload), encoding="utf-8")


def test_client_config_manager_lists_configured_tenants(tmp_path):
    _write_tenant(tmp_path, "travel_alpha", "Travel Alpha")
    _write_tenant(tmp_path, "travel_beta", "Travel Beta")

    manager = ClientConfigManager(tmp_path)

    assert manager.list_client_ids() == ["travel_alpha", "travel_beta"]
    assert manager.client_exists("travel_alpha") is True
    assert manager.client_exists("../bad") is False


def test_unknown_tenant_is_rejected(test_db):
    with pytest.raises(ValueError):
        get_or_create_client_uuid(test_db, "missing_tenant")


def test_get_or_create_client_uses_tenant_config(test_db):
    client_uuid = get_or_create_client_uuid(test_db, "travel_alfalah")
    client = test_db.query(models.Client).filter(models.Client.id == client_uuid).first()

    assert str(client_uuid)
    assert client.brand_name == "Travel Umroh Al-Falah"
    assert client.timezone == "Asia/Jakarta"


def test_channel_factory_uses_tenant_env(monkeypatch):
    monkeypatch.setenv("TENANT_STARSENDER_KEY", "tenant_key")
    monkeypatch.setenv("TENANT_WEBHOOK_SECRET", "tenant_secret")
    adapter = build_channel_adapter("starsender", {
        "channels": {
            "starsender": {
                "enabled": True,
                "api_key_env": "TENANT_STARSENDER_KEY",
                "webhook_secret_env": "TENANT_WEBHOOK_SECRET",
            }
        }
    })

    assert adapter.api_key == "tenant_key"
    assert adapter.validate_signature({}, {"x-webhook-secret": "tenant_secret"}) is True
    assert adapter.validate_signature({}, {"x-webhook-secret": "wrong"}) is False
