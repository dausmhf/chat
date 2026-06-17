import pytest

from app.bootstrap import validate_client_production_config
from app.channels.starsender_adapter import StarsenderAdapter
from app.channels.webhook_security import headers_with_query_secret
from app.config import settings


def test_starsender_rejects_missing_secret_in_production(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.delenv("STARSENDER_WEBHOOK_SECRET", raising=False)

    adapter = StarsenderAdapter(api_key="real_key", webhook_secret="")

    assert adapter.validate_signature({}, {}) is False


def test_starsender_allows_missing_secret_in_development(monkeypatch):
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.delenv("STARSENDER_WEBHOOK_SECRET", raising=False)

    adapter = StarsenderAdapter(api_key="mock_key", webhook_secret="")

    assert adapter.validate_signature({}, {}) is True


def test_webhook_query_secret_is_promoted_to_header():
    headers = headers_with_query_secret({}, {"secret": "abc123"})

    assert headers["x-webhook-secret"] == "abc123"


def test_client_production_config_requires_starsender_secret(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "gemini_real")
    monkeypatch.setenv("STARSENDER_API_KEY", "starsender_real")
    monkeypatch.delenv("STARSENDER_WEBHOOK_SECRET", raising=False)
    configs = {
        "ai": {"api_key_env": "GEMINI_API_KEY"},
        "channel": {
            "active_channel": "starsender",
            "channels": {
                "starsender": {
                    "enabled": True,
                    "api_key_env": "STARSENDER_API_KEY",
                    "webhook_secret_env": "STARSENDER_WEBHOOK_SECRET",
                }
            },
        },
    }

    with pytest.raises(ValueError, match="STARSENDER_WEBHOOK_SECRET"):
        validate_client_production_config("travel_alfalah", configs)


def test_inactive_client_skips_strict_production_env_validation(monkeypatch):
    monkeypatch.delenv("GEMINI_MISSING", raising=False)
    monkeypatch.delenv("STARSENDER_MISSING", raising=False)
    monkeypatch.delenv("STARSENDER_SECRET_MISSING", raising=False)
    configs = {
        "client": {"status": "inactive"},
        "ai": {"api_key_env": "GEMINI_MISSING"},
        "channel": {
            "active_channel": "starsender",
            "channels": {
                "starsender": {
                    "enabled": True,
                    "api_key_env": "STARSENDER_MISSING",
                    "webhook_secret_env": "STARSENDER_SECRET_MISSING",
                }
            },
        },
    }

    validate_client_production_config("paused_tenant", configs)
