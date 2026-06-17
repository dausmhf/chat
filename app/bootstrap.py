import sys
import os
from pathlib import Path
from app.config import settings, client_config_manager
from app.storage.migrate import run_migrations


PLACEHOLDER_VALUES = {"", "mock_key", "xxx", "change_this_dashboard_token"}

def validate_global_config():
    """
    Validates essential environment variables are set and non-empty.
    Fails safely if critical settings are missing.
    """
    critical_vars = [
        ("DATABASE_URL", settings.database_url),
        ("REDIS_URL", settings.redis_url),
    ]
    for var_name, val in critical_vars:
        if not val or val.startswith("your_") or val == "xxx":
            print(f"CRITICAL ERROR: {var_name} configuration is invalid or missing.")
            sys.exit(1)

    if settings.app_env == "production":
        production_vars = [
            ("APP_URL", settings.app_url),
            ("STORAGE_ROOT", settings.storage_root),
            ("GEMINI_API_KEY", settings.gemini_api_key),
            ("ADMIN_DASHBOARD_TOKEN", os.getenv("ADMIN_DASHBOARD_TOKEN", "")),
        ]
        for var_name, val in production_vars:
            if _is_placeholder(val):
                print(f"CRITICAL ERROR: {var_name} must be configured for production.")
                sys.exit(1)

def validate_client_config(client_id: str):
    """
    Validates client folder configuration exists and loads correctly.
    """
    try:
        configs = client_config_manager.load_all_configs(client_id)
        # Validate that client_config matches the requested client_id
        loaded_client_id = configs["client"].get("client_id")
        if loaded_client_id != client_id:
            raise ValueError(f"client_id mismatch in config: found '{loaded_client_id}', expected '{client_id}'")
        if settings.app_env == "production":
            validate_client_production_config(client_id, configs)
        print(f"Bootstrap: Successfully validated configuration for client '{client_id}'")
        return configs
    except Exception as e:
        print(f"CRITICAL ERROR: Client '{client_id}' configuration validation failed: {str(e)}")
        sys.exit(1)


def validate_client_production_config(client_id: str, configs: dict) -> None:
    ai_config = configs.get("ai", {})
    ai_key_env = ai_config.get("api_key_env", "GEMINI_API_KEY")
    if _is_placeholder(os.getenv(ai_key_env, "")):
        raise ValueError(f"{ai_key_env} must be set for tenant '{client_id}'.")

    channel_config = configs.get("channel", {})
    active_channel = channel_config.get("active_channel", "starsender")
    channel_settings = channel_config.get("channels", {}).get(active_channel, {})
    if not channel_settings.get("enabled", False):
        raise ValueError(f"Active channel '{active_channel}' is disabled.")
    if active_channel == "starsender":
        api_key_env = channel_settings.get("api_key_env", "STARSENDER_API_KEY")
        webhook_secret_env = channel_settings.get("webhook_secret_env", "STARSENDER_WEBHOOK_SECRET")
        if _is_placeholder(os.getenv(api_key_env, "")):
            raise ValueError(f"{api_key_env} must be set for tenant '{client_id}'.")
        if _is_placeholder(os.getenv(webhook_secret_env, "")):
            raise ValueError(f"{webhook_secret_env} must be set for tenant '{client_id}'.")


def _is_placeholder(value: str) -> bool:
    value = (value or "").strip()
    return value in PLACEHOLDER_VALUES or value.startswith("your_")

def bootstrap_app():
    """
    Initializes system bootstrap validations.
    """
    print("Bootstrap: Initializing system validation checks...")
    validate_global_config()
    
    # Run DB migration schema updates
    try:
        run_migrations()
    except Exception as e:
        print(f"Bootstrap: Warning - migration check failed (database might be offline): {str(e)}")

    client_ids = client_config_manager.list_client_ids()
    if not client_ids:
        print("CRITICAL ERROR: No configured clients found under clients/*/config.")
        sys.exit(1)
    for client_id in client_ids:
        validate_client_config(client_id)
    print("Bootstrap: System checks passed successfully.")

if __name__ == "__main__":
    bootstrap_app()
