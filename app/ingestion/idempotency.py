import datetime
import hashlib
import json
import uuid
from typing import Optional, Tuple
from sqlalchemy.orm import Session
from app.storage.repositories import IdempotencyRepository

def generate_payload_hash(payload: dict) -> str:
    """
    Generates a deterministic hash from a JSON payload.
    """
    serialized = json.dumps(payload, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()

def verify_and_log_idempotency(
    db: Session,
    client_id: uuid.UUID,
    channel: str,
    idempotency_key: str,
    event_type: str,
    payload: dict,
    ttl_days: int = 30
) -> Tuple[bool, str]:
    """
    Verifies if a webhook payload is unique.
    Returns:
        (is_unique, payload_hash)
    """
    payload_hash = generate_payload_hash(payload)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=ttl_days)
    
    repo = IdempotencyRepository(db, client_id)
    is_unique = repo.check_and_insert(
        idempotency_key=idempotency_key,
        channel=channel,
        event_type=event_type,
        expires_at=expires_at,
        raw_payload_hash=payload_hash
    )
    return is_unique, payload_hash
