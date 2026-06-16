import time
import os
import httpx
from typing import Optional
from app.config import settings
from app.channels.base_adapter import ChannelAdapter, MessageEvent, SendResult, SenderIdentity, ChannelHealthResult

class StarsenderAdapter(ChannelAdapter):
    def __init__(self, api_key: str = settings.starsender_api_key):
        self.api_key = api_key
        self.api_url = "https://starsender.id/api/v2/send" # Example API endpoint for Starsender

    def parse_incoming(self, payload: dict) -> MessageEvent:
        """
        Parses Starsender incoming webhook payload into a normalized MessageEvent.
        """
        # Mapping expected Starsender format:
        # {
        #   "messageId": "starsender_msg_123",
        #   "message": "Saya mau tanya paket Oktober",
        #   "phone": "628123456789",
        #   "name": "Muhammad Firdaus",
        #   "client_id": "travel_alfalah" (or resolved by context)
        # }
        msg_id = payload.get("messageId") or payload.get("message_id") or payload.get("id") or payload.get("key", {}).get("id", "unknown_id")
        phone = payload.get("phone") or payload.get("from") or payload.get("sender") or payload.get("remoteJid", "unknown_phone")
        name = payload.get("name") or payload.get("pushName") or payload.get("senderName", "WhatsApp User")
        text = payload.get("message") or payload.get("text") or payload.get("caption") or ""
        client_id = payload.get("client_id", "travel_alfalah")
        file_url = payload.get("fileUrl") or payload.get("file_url") or payload.get("mediaUrl") or payload.get("url") or ""
        raw_type = (payload.get("messageType") or payload.get("type") or "").lower()
        if raw_type in {"image", "document", "file"}:
            message_type = "image" if raw_type == "image" else "file"
        elif file_url:
            message_type = "image" if any(token in file_url.lower() for token in [".jpg", ".jpeg", ".png", ".webp"]) else "file"
        else:
            message_type = "text"

        # Fallback idempotency key
        idempotency_key = f"starsender:{msg_id}"

        return MessageEvent(
            event_id=msg_id,
            idempotency_key=idempotency_key,
            client_id=client_id,
            channel="starsender",
            sender_name=name,
            sender_phone=phone,
            message_type=message_type,
            text=text,
            file_url=file_url,
            raw_payload=payload
        )

    def send_text(self, conversation_id: str, text: str) -> SendResult:
        """
        Sends text message to WhatsApp via Starsender.
        """
        payload = {
            "apikey": self.api_key,
            "to": conversation_id,
            "message": text
        }
        try:
            # Under mock environment we bypass actual endpoint call if mock_key is used
            if self.api_key == "mock_key":
                return SendResult(success=True, message_id="mock_starsender_msg_id")

            # Actual call
            response = httpx.post(self.api_url, json=payload, timeout=10.0)
            if response.status_code == 200:
                resp_json = response.json()
                if resp_json.get("status") == "success" or resp_json.get("message") == "success":
                    return SendResult(success=True, message_id=resp_json.get("messageId"))
                return SendResult(success=False, error_message=resp_json.get("error", "Failed request"))
            return SendResult(success=False, error_message=f"HTTP Status {response.status_code}")
        except Exception as e:
            return SendResult(success=False, error_message=str(e))

    def send_file(self, conversation_id: str, file_path: str, caption: Optional[str] = None) -> SendResult:
        """
        Sends document/file to WhatsApp via Starsender.
        """
        # Starsender usually sends files via URL
        # Under mock or local path, we'd upload or link. Here we mock:
        payload = {
            "apikey": self.api_key,
            "to": conversation_id,
            "file": file_path,
            "caption": caption
        }
        try:
            if self.api_key == "mock_key":
                return SendResult(success=True, message_id="mock_starsender_file_id")

            response = httpx.post(f"{self.api_url}/file", json=payload, timeout=15.0)
            if response.status_code == 200:
                resp_json = response.json()
                return SendResult(success=True, message_id=resp_json.get("messageId"))
            return SendResult(success=False, error_message=f"HTTP Status {response.status_code}")
        except Exception as e:
            return SendResult(success=False, error_message=str(e))

    def mark_read(self, conversation_id: str) -> None:
        pass

    def get_sender_identity(self, payload: dict) -> SenderIdentity:
        phone = payload.get("phone", "unknown")
        name = payload.get("name", "WhatsApp User")
        return SenderIdentity(phone=phone, name=name, channel_user_id=phone)

    def validate_signature(self, payload: dict, headers: dict) -> bool:
        expected_secret = os.getenv("STARSENDER_WEBHOOK_SECRET")
        if not expected_secret:
            return True
        provided = headers.get("x-webhook-secret") or headers.get("X-Webhook-Secret") or headers.get("x-starsender-secret")
        return provided == expected_secret

    def health_check(self) -> ChannelHealthResult:
        """
        Validates Starsender credentials status.
        """
        start_time = time.time()
        if self.api_key == "mock_key":
            return ChannelHealthResult(
                status="healthy",
                provider="starsender",
                can_send_message=True,
                can_receive_webhook=True,
                credential_valid=True,
                latency_ms=1
            )
        
        # Test ping to provider
        try:
            response = httpx.get("https://starsender.id/api/ping", params={"apikey": self.api_key}, timeout=5.0)
            latency = int((time.time() - start_time) * 1000)
            is_valid = response.status_code == 200
            return ChannelHealthResult(
                status="healthy" if is_valid else "unhealthy",
                provider="starsender",
                can_send_message=is_valid,
                can_receive_webhook=True,
                credential_valid=is_valid,
                latency_ms=latency
            )
        except Exception as e:
            latency = int((time.time() - start_time) * 1000)
            return ChannelHealthResult(
                status="unhealthy",
                provider="starsender",
                can_send_message=False,
                can_receive_webhook=False,
                credential_valid=False,
                latency_ms=latency,
                error_code="CONNECTION_ERROR",
                recommended_action=str(e)
            )
