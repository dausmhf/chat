"""
Secrets Manager — per-tenant API key storage.

Keys are stored in {tenant_dir}/config/.secrets.json with base64 obfuscation.
Not meant for enterprise-grade security — suitable for self-hosted / development.
For production, integrate with a vault service.
"""

import base64
import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.config import client_config_manager

# Known API key fields and their fallback env variable names
KNOWN_KEYS = {
    "gemini_api_key": "GEMINI_API_KEY",
    "starsender_api_key": "STARSENDER_API_KEY",
    "starsender_webhook_secret": "STARSENDER_WEBHOOK_SECRET",
    "waba_access_token": "WABA_ACCESS_TOKEN",
    "waba_phone_number_id": "WABA_PHONE_NUMBER_ID",
    "waba_verify_token": "WABA_VERIFY_TOKEN",
    "telegram_bot_token": "TELEGRAM_BOT_TOKEN",
}


def _secrets_path(client_code: str) -> Path:
    """Return path to the tenant's secrets file."""
    return client_config_manager.get_writable_client_dir(client_code) / "config" / ".secrets.json"


def _encode(value: str) -> str:
    """Obfuscate a value with base64."""
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _decode(value: str) -> str:
    """De-obfuscate a base64 value."""
    try:
        return base64.b64decode(value.encode("ascii")).decode("utf-8")
    except Exception:
        return value


def _load_secrets(client_code: str) -> Dict[str, str]:
    """Load the secrets file for a tenant. Returns empty dict if not found."""
    path = _secrets_path(client_code)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return {k: _decode(v) for k, v in raw.items() if isinstance(v, str)}
    except (json.JSONDecodeError, Exception):
        return {}


def _save_secrets(client_code: str, secrets: Dict[str, str]) -> None:
    """Save the secrets file for a tenant."""
    path = _secrets_path(client_code)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = {k: _encode(v) for k, v in secrets.items() if v}
    path.write_text(json.dumps(encoded, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def get_secret(client_code: str, key_name: str) -> Optional[str]:
    """
    Get a secret value for a tenant.
    Priority: .secrets.json > env variable (from config) > global env > None
    """
    # 1. Check tenant secrets file
    secrets = _load_secrets(client_code)
    if key_name in secrets and secrets[key_name]:
        return secrets[key_name]

    # 2. Check tenant config for env var name, then resolve from env
    env_var_name = _get_env_var_name(client_code, key_name)
    if env_var_name:
        val = os.getenv(env_var_name, "")
        if val and val not in {"", "mock_key", "xxx"}:
            return val

    # 3. Fallback to known global env var
    fallback_env = KNOWN_KEYS.get(key_name)
    if fallback_env:
        val = os.getenv(fallback_env, "")
        if val and val not in {"", "mock_key", "xxx"}:
            return val

    return None


def set_secret(client_code: str, key_name: str, value: str) -> None:
    """Set a secret value for a tenant."""
    secrets = _load_secrets(client_code)
    if value and value.strip():
        secrets[key_name] = value.strip()
    else:
        secrets.pop(key_name, None)
    _save_secrets(client_code, secrets)


def set_secrets_bulk(client_code: str, keys: Dict[str, str]) -> Dict[str, str]:
    """Set multiple secrets at once. Returns updated key list (masked)."""
    secrets = _load_secrets(client_code)
    for key_name, value in keys.items():
        if key_name not in KNOWN_KEYS:
            continue
        if value and value.strip():
            secrets[key_name] = value.strip()
        else:
            secrets.pop(key_name, None)
    _save_secrets(client_code, secrets)
    return get_secrets_masked(client_code)


def delete_secret(client_code: str, key_name: str) -> None:
    """Remove a secret value for a tenant."""
    secrets = _load_secrets(client_code)
    secrets.pop(key_name, None)
    _save_secrets(client_code, secrets)


def get_secrets_masked(client_code: str) -> Dict[str, str]:
    """
    Get all known secrets for a tenant, masked for display.
    Shows the source of each key: 'secrets_file', 'env_var', or 'not_set'.
    """
    result = {}
    for key_name in KNOWN_KEYS:
        secrets = _load_secrets(client_code)
        if key_name in secrets and secrets[key_name]:
            val = secrets[key_name]
            result[key_name] = {
                "source": "secrets_file",
                "masked_value": _mask(val),
                "is_set": True,
            }
        else:
            resolved = get_secret(client_code, key_name)
            if resolved:
                result[key_name] = {
                    "source": "env_var",
                    "masked_value": _mask(resolved),
                    "is_set": True,
                }
            else:
                result[key_name] = {
                    "source": "not_set",
                    "masked_value": "",
                    "is_set": False,
                }
    return result


def delete_all_secrets(client_code: str) -> None:
    """Remove the entire secrets file for a tenant."""
    path = _secrets_path(client_code)
    if path.exists():
        path.unlink()


def _mask(value: str) -> str:
    """Mask a secret value for display, showing first 4 and last 4 chars."""
    if not value:
        return ""
    if len(value) <= 8:
        return value[:2] + "•" * (len(value) - 2)
    return value[:4] + "•" * (len(value) - 8) + value[-4:]


def _get_env_var_name(client_code: str, key_name: str) -> Optional[str]:
    """Get the env variable name configured in tenant config for a given key."""
    try:
        if key_name in {"gemini_api_key"}:
            ai_config = client_config_manager.load_json_config(client_code, "ai_config.json")
            return ai_config.get("api_key_env")
        elif key_name in {"starsender_api_key"}:
            channel_config = client_config_manager.load_json_config(client_code, "channel_config.json")
            return channel_config.get("channels", {}).get("starsender", {}).get("api_key_env")
        elif key_name in {"starsender_webhook_secret"}:
            channel_config = client_config_manager.load_json_config(client_code, "channel_config.json")
            return channel_config.get("channels", {}).get("starsender", {}).get("webhook_secret_env")
        elif key_name in {"waba_access_token"}:
            channel_config = client_config_manager.load_json_config(client_code, "channel_config.json")
            return channel_config.get("channels", {}).get("waba", {}).get("access_token_env")
        elif key_name in {"waba_phone_number_id"}:
            channel_config = client_config_manager.load_json_config(client_code, "channel_config.json")
            return channel_config.get("channels", {}).get("waba", {}).get("phone_number_id_env")
        elif key_name in {"waba_verify_token"}:
            channel_config = client_config_manager.load_json_config(client_code, "channel_config.json")
            return channel_config.get("channels", {}).get("waba", {}).get("verify_token_env")
        elif key_name in {"telegram_bot_token"}:
            return "TELEGRAM_BOT_TOKEN"
    except Exception:
        pass
    return KNOWN_KEYS.get(key_name)
