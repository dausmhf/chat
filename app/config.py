import os
import json
from pathlib import Path
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field
from dotenv import load_dotenv

# Load .env file if present
load_dotenv()

class GlobalSettings(BaseModel):
    app_env: str = Field(default="development", alias="APP_ENV")
    app_url: str = Field(default="http://localhost:8000", alias="APP_URL")
    database_url: str = Field(default="postgresql://postgres:postgres@localhost:5432/aics", alias="DATABASE_URL")
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    storage_root: str = Field(default="./storage", alias="STORAGE_ROOT")
    llm_api_key: str = Field(default="mock_key", alias="LLM_API_KEY")
    gemini_api_key: str = Field(default="mock_key", alias="GEMINI_API_KEY")
    starsender_api_key: str = Field(default="mock_key", alias="STARSENDER_API_KEY")
    waba_access_token: str = Field(default="mock_key", alias="WABA_ACCESS_TOKEN")
    telegram_bot_token: str = Field(default="mock_key", alias="TELEGRAM_BOT_TOKEN")
    admin_default_group_id: str = Field(default="-100123456789", alias="ADMIN_DEFAULT_GROUP_ID")

    class Config:
        allow_population_by_field_name = True

# Instantiate global settings
settings = GlobalSettings(
    APP_ENV=os.getenv("APP_ENV", "development"),
    APP_URL=os.getenv("APP_URL", "http://localhost:8000"),
    DATABASE_URL=os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/aics"),
    REDIS_URL=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    STORAGE_ROOT=os.getenv("STORAGE_ROOT", "./storage"),
    LLM_API_KEY=os.getenv("LLM_API_KEY", "mock_key"),
    GEMINI_API_KEY=os.getenv("GEMINI_API_KEY", "mock_key"),
    STARSENDER_API_KEY=os.getenv("STARSENDER_API_KEY", "mock_key"),
    WABA_ACCESS_TOKEN=os.getenv("WABA_ACCESS_TOKEN", "mock_key"),
    TELEGRAM_BOT_TOKEN=os.getenv("TELEGRAM_BOT_TOKEN", "mock_key"),
    ADMIN_DEFAULT_GROUP_ID=os.getenv("ADMIN_DEFAULT_GROUP_ID", "-100123456789"),
)

class ClientConfigManager:
    """
    Manages loading of client-specific configurations from clients/{client_id}/config/
    """
    def __init__(self, base_dir: Optional[Path] = None):
        if base_dir is None:
            # Default to the directory where clients folder sits
            self.base_dir = Path(__file__).parent.parent.resolve()
        else:
            self.base_dir = Path(base_dir)

    def get_client_dir(self, client_id: str) -> Path:
        return self.base_dir / "clients" / client_id

    def load_json_config(self, client_id: str, filename: str) -> Dict[str, Any]:
        path = self.get_client_dir(client_id) / "config" / filename
        if not path.exists():
            raise FileNotFoundError(f"Config file {filename} not found for client {client_id} at {path}")
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_all_configs(self, client_id: str) -> Dict[str, Any]:
        """
        Loads client_config, channel_config, payment_config, ai_config, and feature_flags
        """
        configs = {}
        files = {
            "client": "client_config.json",
            "channel": "channel_config.json",
            "payment": "payment_config.json",
            "ai": "ai_config.json",
            "feature_flags": "feature_flags.json",
        }
        for key, filename in files.items():
            try:
                configs[key] = self.load_json_config(client_id, filename)
            except FileNotFoundError as e:
                # Fallback or raise
                raise ValueError(f"Failed to load client config for {client_id}: {str(e)}")
        return configs

# Instantiate client config manager
client_config_manager = ClientConfigManager()
