import sys
from pathlib import Path
from app.config import settings, client_config_manager
from app.storage.migrate import run_migrations

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
        print(f"Bootstrap: Successfully validated configuration for client '{client_id}'")
        return configs
    except Exception as e:
        print(f"CRITICAL ERROR: Client '{client_id}' configuration validation failed: {str(e)}")
        sys.exit(1)

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
