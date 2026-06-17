from app.admin.tenant_service import create_tenant, is_tenant_active, set_tenant_status, update_tenant
from app.config import client_config_manager
from app.storage import models


def test_create_tenant_writes_persistent_config_and_db_row(test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(client_config_manager, "storage_dir", tmp_path)
    monkeypatch.setattr(client_config_manager, "base_dir", tmp_path)

    result = create_tenant(
        test_db,
        client_code="travel_baru",
        brand_name="Travel Baru",
        bot_name="AI Travel Baru",
        admin_phone="628111",
        starsender_api_key_env="STARSENDER_TRAVEL_BARU",
        gemini_api_key_env="GEMINI_TRAVEL_BARU",
    )

    client = test_db.query(models.Client).filter_by(client_code="travel_baru").first()
    assert client is not None
    assert client.brand_name == "Travel Baru"
    assert result["webhook_url"] == "/webhooks/starsender/travel_baru"
    assert (tmp_path / "clients" / "travel_baru" / "config" / "client_config.json").exists()
    channel_config = (tmp_path / "clients" / "travel_baru" / "config" / "channel_config.json").read_text(encoding="utf-8")
    assert "STARSENDER_TRAVEL_BARU" in channel_config
    assert "api key" not in channel_config.lower()


def test_super_admin_can_disable_enable_and_edit_tenant(test_db, tmp_path, monkeypatch):
    monkeypatch.setattr(client_config_manager, "storage_dir", tmp_path)
    monkeypatch.setattr(client_config_manager, "base_dir", tmp_path)
    create_tenant(
        test_db,
        client_code="travel_edit",
        brand_name="Travel Edit",
        bot_name="AI Lama",
        admin_phone="628111",
    )

    disabled = set_tenant_status(test_db, "travel_edit", "inactive")
    assert disabled["status"] == "inactive"
    assert is_tenant_active("travel_edit") is False

    edited = update_tenant(
        test_db,
        client_code="travel_edit",
        brand_name="Travel Baru Edit",
        bot_name="AI Baru",
        admin_notification_phone="628222",
        starsender_api_key_env="STARSENDER_EDIT",
        starsender_webhook_secret_env="STARSENDER_SECRET_EDIT",
        gemini_api_key_env="GEMINI_EDIT",
    )
    assert edited["brand_name"] == "Travel Baru Edit"
    assert edited["bot_name"] == "AI Baru"
    assert edited["admin_notification_phone"] == "628222"
    assert edited["starsender_api_key_env"] == "STARSENDER_EDIT"
    assert edited["starsender_webhook_secret_env"] == "STARSENDER_SECRET_EDIT"
    assert edited["gemini_api_key_env"] == "GEMINI_EDIT"

    enabled = set_tenant_status(test_db, "travel_edit", "active")
    assert enabled["status"] == "active"
    assert is_tenant_active("travel_edit") is True

    client = test_db.query(models.Client).filter_by(client_code="travel_edit").first()
    assert client.status == "active"
    assert client.brand_name == "Travel Baru Edit"
