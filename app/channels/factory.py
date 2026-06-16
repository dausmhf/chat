import os
from typing import Any, Dict, Optional

from app.channels.starsender_adapter import StarsenderAdapter
from app.channels.waba_adapter import WabaCloudAdapter


def build_channel_adapter(channel: str, channel_config: Optional[Dict[str, Any]] = None):
    config = (channel_config or {}).get("channels", {}).get(channel, {})
    if channel == "starsender":
        return StarsenderAdapter(
            api_key=_env_value(config.get("api_key_env"), "mock_key"),
            webhook_secret=_env_value(config.get("webhook_secret_env"), ""),
        )
    if channel == "waba":
        return WabaCloudAdapter(
            access_token=_env_value(config.get("access_token_env"), "mock_key"),
            phone_number_id=_env_value(config.get("phone_number_id_env"), ""),
            verify_token=_env_value(config.get("verify_token_env"), ""),
        )
    raise ValueError(f"Unsupported channel: {channel}")


def _env_value(env_name: Optional[str], default: str) -> str:
    if not env_name:
        return default
    return os.getenv(env_name, default)
