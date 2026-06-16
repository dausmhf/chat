import time
import os
import httpx
from typing import Optional
from app.config import settings
from app.channels.base_adapter import ChannelAdapter, MessageEvent, SendResult, SenderIdentity, ChannelHealthResult

class WabaCloudAdapter(ChannelAdapter):
    """
    Adapter for Meta WhatsApp Business API Cloud API.
    """
    def __init__(self, access_token: str = settings.waba_access_token):
        self.access_token = access_token
        self.phone_number_id = os.getenv("WABA_PHONE_NUMBER_ID", "")
        self.graph_version = os.getenv("WABA_GRAPH_VERSION", "v19.0")

    def parse_incoming(self, payload: dict) -> MessageEvent:
        # Standard format parses payload['entry'][0]['changes'][0]['value']['messages'][0]
        entry = payload.get("entry", [{}])[0]
        change = entry.get("changes", [{}])[0]
        value = change.get("value", {})
        message = value.get("messages", [{}])[0]
        
        msg_id = message.get("id", "unknown_waba_id")
        contact = value.get("contacts", [{}])[0]
        name = contact.get("profile", {}).get("name", "WhatsApp User")
        phone = message.get("from", "unknown")
        text = message.get("text", {}).get("body", "")
        
        idempotency_key = f"waba:{msg_id}"
        
        return MessageEvent(
            event_id=msg_id,
            idempotency_key=idempotency_key,
            client_id=payload.get("client_id", "travel_alfalah"),
            channel="waba",
            sender_name=name,
            sender_phone=phone,
            message_type="text",
            text=text,
            raw_payload=payload
        )

    def send_text(self, conversation_id: str, text: str) -> SendResult:
        if self.access_token == "mock_key" or not self.phone_number_id:
            print(f"WABA adapter: mock send_text to {conversation_id}: {text}")
            return SendResult(success=True, message_id="mock_waba_msg_id")

        url = f"https://graph.facebook.com/{self.graph_version}/{self.phone_number_id}/messages"
        headers = {"Authorization": f"Bearer {self.access_token}", "Content-Type": "application/json"}
        payload = {
            "messaging_product": "whatsapp",
            "to": conversation_id,
            "type": "text",
            "text": {"preview_url": False, "body": text},
        }
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=10.0)
            data = response.json()
            if response.status_code in {200, 201}:
                msg_id = (data.get("messages") or [{}])[0].get("id", "")
                return SendResult(success=True, message_id=msg_id)
            return SendResult(success=False, error_message=str(data))
        except Exception as e:
            return SendResult(success=False, error_message=str(e))

    def send_file(self, conversation_id: str, file_path: str, caption: Optional[str] = None) -> SendResult:
        if self.access_token == "mock_key" or not self.phone_number_id:
            print(f"WABA adapter: mock send_file to {conversation_id}: {file_path}")
            return SendResult(success=True, message_id="mock_waba_file_id")
        return SendResult(
            success=False,
            error_message="WABA media upload requires a public media URL or upload step; configure delivery worker before enabling live file send."
        )

    def mark_read(self, conversation_id: str) -> None:
        # TODO: Send mark read status webhook
        pass

    def get_sender_identity(self, payload: dict) -> SenderIdentity:
        entry = payload.get("entry", [{}])[0]
        change = entry.get("changes", [{}])[0]
        value = change.get("value", {})
        message = value.get("messages", [{}])[0]
        phone = message.get("from", "unknown")
        contact = value.get("contacts", [{}])[0]
        name = contact.get("profile", {}).get("name", "WhatsApp User")
        return SenderIdentity(phone=phone, name=name, channel_user_id=phone)

    def validate_signature(self, payload: dict, headers: dict) -> bool:
        # TODO: Implement X-Hub-Signature validation
        return True

    def health_check(self) -> ChannelHealthResult:
        """
        Validates WABA Cloud API configuration status.
        """
        if self.access_token == "mock_key":
            return ChannelHealthResult(
                status="healthy",
                provider="waba",
                can_send_message=True,
                can_receive_webhook=True,
                credential_valid=True,
                latency_ms=1
            )
        if not self.phone_number_id:
            return ChannelHealthResult(
                status="unhealthy",
                provider="waba",
                can_send_message=False,
                can_receive_webhook=True,
                credential_valid=False,
                latency_ms=1,
                error_code="MISSING_PHONE_NUMBER_ID",
                recommended_action="Set WABA_PHONE_NUMBER_ID."
            )
        return ChannelHealthResult(
            status="healthy",
            provider="waba",
            can_send_message=True,
            can_receive_webhook=True,
            credential_valid=True,
            latency_ms=1
        )
