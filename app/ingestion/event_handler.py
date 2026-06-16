import datetime
import uuid
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.storage import models
from app.config import client_config_manager
from app.ingestion.message_normalizer import normalize_payload
from app.ingestion.idempotency import verify_and_log_idempotency
from app.ingestion.contact_resolver import resolve_contact_and_lead
from app.storage.repositories import ConversationRepository, MessageRepository

def get_or_create_client_uuid(db: Session, client_code: str) -> uuid.UUID:
    """
    Retrieves the client UUID for a configured client code, creating the DB row from
    clients/{client_code}/config/client_config.json when needed.
    """
    if not client_config_manager.client_exists(client_code):
        raise ValueError(f"Unknown client_code '{client_code}'.")

    configs = client_config_manager.load_all_configs(client_code)
    client_config = configs["client"]
    client = db.query(models.Client).filter(models.Client.client_code == client_code).first()
    if not client:
        client = models.Client(
            client_code=client_code,
            name=client_config.get("client_name") or client_config.get("brand_name") or client_code,
            brand_name=client_config.get("brand_name") or client_config.get("client_name") or client_code,
            timezone=client_config.get("timezone", "Asia/Jakarta"),
            status=client_config.get("status", "active"),
        )
        db.add(client)
        db.commit()
        db.refresh(client)
    else:
        client.name = client_config.get("client_name") or client.name
        client.brand_name = client_config.get("brand_name") or client.brand_name
        client.timezone = client_config.get("timezone", client.timezone)
        client.status = client_config.get("status", client.status)
        db.commit()
    return client.id

def handle_incoming_webhook(
    db: Session,
    channel: str,
    payload: dict,
    headers: Optional[dict] = None
) -> Dict[str, Any]:
    """
    Main webhook ingestion entry point.
    Runs idempotency checking, captures raw/normalized payloads, and logs message.
    """
    headers = headers or {}
    
    # 1. Resolve client code (defaulting to travel_alfalah for sandbox)
    client_code = payload.get("client_id", "travel_alfalah")
    client_uuid = get_or_create_client_uuid(db, client_code)
    
    # 2. Normalize payload
    try:
        event = normalize_payload(channel, payload, client_code)
    except Exception as e:
        print(f"EventHandler: Normalization failed for payload: {str(e)}")
        return {"status": "error", "message": f"Normalization failed: {str(e)}"}
        
    if not event:
        return {"status": "error", "message": "Normalization returned empty event"}

    # 3. Check webhook idempotency
    is_unique, payload_hash = verify_and_log_idempotency(
        db=db,
        client_id=client_uuid,
        channel=channel,
        idempotency_key=event.idempotency_key,
        event_type=event.message_type,
        payload=payload
    )
    
    if not is_unique:
        print(f"EventHandler: Duplicate webhook detected for key '{event.idempotency_key}'. Skipping processing.")
        return {"status": "duplicate", "message": "Ignored duplicate webhook payload"}

    # 4. Save raw webhook payload
    raw_payload = models.RawWebhookPayload(
        client_id=client_uuid,
        channel=channel,
        payload=payload,
        headers=headers,
        payload_hash=payload_hash
    )
    db.add(raw_payload)
    db.commit()
    db.refresh(raw_payload)

    # 5. Resolve Contact and Lead Profile
    contact, lead_profile = resolve_contact_and_lead(
        db=db,
        client_id=client_uuid,
        phone_e164=event.sender_phone,
        sender_name=event.sender_name,
        channel=channel
    )

    # 6. Resolve Conversation
    conv_repo = ConversationRepository(db, client_uuid)
    conversation = conv_repo.create_or_update(
        contact_id=contact.id,
        channel=channel,
        last_message_at=datetime.datetime.now(datetime.timezone.utc)
    )

    # 7. Log incoming message
    msg_repo = MessageRepository(db, client_uuid)
    message = msg_repo.log_message(
        conversation_id=conversation.id,
        direction="incoming",
        message_type=event.message_type,
        text_content=event.text,
        file_url=event.file_url,
        provider_message_id=event.event_id,
        contact_id=contact.id,
        raw_payload_id=raw_payload.id,
        metadata={"normalized": True}
    )

    # Update idempotency key with related message_id
    idemp_record = db.query(models.WebhookIdempotencyKey).filter(
        models.WebhookIdempotencyKey.client_id == client_uuid,
        models.WebhookIdempotencyKey.channel == channel,
        models.WebhookIdempotencyKey.idempotency_key == event.idempotency_key
    ).first()
    if idemp_record:
        idemp_record.related_message_id = message.id
        db.commit()

    print(f"EventHandler: Message ingested successfully. Msg ID: {message.id}, Client: {client_code}")
    
    return {
        "status": "success",
        "message_id": str(message.id),
        "conversation_id": str(conversation.id),
        "bot_enabled": conversation.bot_enabled,
        "conversation_status": conversation.status
    }
