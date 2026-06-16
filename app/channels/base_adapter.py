import datetime
from typing import Optional, Dict, Any
from pydantic import BaseModel, Field, root_validator

class MessageEvent(BaseModel):
    event_id: str = ""
    idempotency_key: str = ""
    client_id: str = ""
    channel: str = ""
    channel_account_id: str = ""
    sender_name: str = ""
    sender_phone: str = ""
    message_type: str = ""  # text, image, file, audio, location
    text: str = ""
    file_url: str = ""
    raw_payload: dict = {}
    received_at: str = ""

    @root_validator(pre=True, allow_reuse=True)
    def set_received_at(cls, values):
        if not values.get("received_at"):
            values["received_at"] = datetime.datetime.utcnow().isoformat()
        elif isinstance(values.get("received_at"), datetime.datetime):
            values["received_at"] = values["received_at"].isoformat()
        return values

class SendResult(BaseModel):
    success: bool = False
    message_id: str = ""
    error_message: str = ""

class SenderIdentity(BaseModel):
    phone: str = ""
    name: str = ""
    channel_user_id: str = ""

class ChannelHealthResult(BaseModel):
    status: str = ""  # healthy, degraded, unhealthy
    provider: str = ""
    can_send_message: bool = False
    can_receive_webhook: bool = False
    credential_valid: bool = False
    latency_ms: int = 0
    checked_at: str = ""
    error_code: str = ""
    recommended_action: str = ""

    @root_validator(pre=True, allow_reuse=True)
    def set_checked_at(cls, values):
        if not values.get("checked_at"):
            values["checked_at"] = datetime.datetime.utcnow().isoformat()
        elif isinstance(values.get("checked_at"), datetime.datetime):
            values["checked_at"] = values["checked_at"].isoformat()
        return values

class ChannelAdapter:
    def parse_incoming(self, payload: dict) -> MessageEvent:
        raise NotImplementedError

    def send_text(self, conversation_id: str, text: str) -> SendResult:
        raise NotImplementedError

    def send_file(self, conversation_id: str, file_path: str, caption: Optional[str] = None) -> SendResult:
        raise NotImplementedError

    def mark_read(self, conversation_id: str) -> None:
        raise NotImplementedError

    def get_sender_identity(self, payload: dict) -> SenderIdentity:
        raise NotImplementedError

    def validate_signature(self, payload: dict, headers: dict) -> bool:
        raise NotImplementedError

    def health_check(self) -> ChannelHealthResult:
        raise NotImplementedError
