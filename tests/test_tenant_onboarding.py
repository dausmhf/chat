from app.admin.tenant_service import create_tenant
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
