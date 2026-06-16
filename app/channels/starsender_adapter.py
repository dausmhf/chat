import time
import os
import httpx
from typing import Optional
from app.config import settings
from app.channels.base_adapter import ChannelAdapter, MessageEvent, SendResult, SenderIdentity, ChannelHealthResult

class StarsenderAdapter(ChannelAdapter):
    def __init__(self, api_key: str = settings.starsender_api_key):
        self.api_key = api_key
        self.api_url = "https://starsender.online/api"

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
            "tujuan": conversation_id,
            "message": text
        }
        try:
            # Under mock environment we bypass actual endpoint call if mock_key is used
            if self.api_key == "mock_key":
                return SendResult(success=True, message_id="mock_starsender_msg_id")

            headers = {"apikey": self.api_key}
            response = httpx.post(f"{self.api_url}/sendText", headers=headers, data=payload, timeout=10.0)
            if response.status_code not in {200, 201}:
                return SendResult(success=False, error_message=f"HTTP Status {response.status_code}: {response.text[:200]}")

            resp_json = _safe_json(response)
            if _looks_successful(resp_json, response.text):
                return SendResult(
                    success=True,
                    message_id=_response_message_id(resp_json),
                )
            return SendResult(success=False, error_message=str(resp_json or response.text[:200] or "Failed request"))
        except Exception as e:
            return SendResult(success=False, error_message=str(e))

    def send_file(self, conversation_id: str, file_path: str, caption: Optional[str] = None) -> SendResult:
        """
        Sends document/file to WhatsApp via Starsender.
        """
        # Starsender usually sends files via URL
        # Under mock or local path, we'd upload or link. Here we mock:
        payload = {
            "tujuan": conversation_id,
            "file": file_path,
            "caption": caption
        }
        try:
            if self.api_key == "mock_key":
                return SendResult(success=True, message_id="mock_starsender_file_id")

            response = httpx.post(f"{self.api_url}/sendFile", headers={"apikey": self.api_key}, data=payload, timeout=15.0)
            if response.status_code not in {200, 201}:
                return SendResult(success=False, error_message=f"HTTP Status {response.status_code}: {response.text[:200]}")
            resp_json = _safe_json(response)
            if _looks_successful(resp_json, response.text):
                return SendResult(
                    success=True,
                    message_id=_response_message_id(resp_json),
                )
            return SendResult(success=False, error_message=str(resp_json or response.text[:200] or "Failed request"))
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
        
        latency = int((time.time() - start_time) * 1000)
        return ChannelHealthResult(
            status="degraded",
            provider="starsender",
            can_send_message=True,
            can_receive_webhook=True,
            credential_valid=True,
            latency_ms=latency,
            recommended_action="Starsender does not expose a non-sending ping endpoint in this adapter; credentials are verified when sending."
        )


def _safe_json(response: httpx.Response) -> dict:
    try:
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}
    except ValueError:
        return {}


def _looks_successful(payload: dict, raw_text: str) -> bool:
    status = str(payload.get("status") or payload.get("success") or payload.get("message") or "").lower()
    if status in {"true", "success", "sent", "ok", "200"}:
        return True
    return "success" in raw_text.lower() or "terkirim" in raw_text.lower()


def _response_message_id(payload: dict) -> str:
    data = payload.get("data")
    data_id = data.get("id") if isinstance(data, dict) else ""
    return str(payload.get("messageId") or payload.get("id") or data_id or "")
