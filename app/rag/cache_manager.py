import datetime
import hashlib
import uuid
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.storage import models


def make_cache_key(client_id: uuid.UUID, normalized_query: str, knowledge_version: str) -> str:
    raw = f"{client_id}:{normalized_query}:{knowledge_version}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def get_cached_answer(
    db: Session,
    client_id: uuid.UUID,
    normalized_query: str,
    knowledge_version: str,
) -> Optional[models.RagResponseCache]:
    cache_key = make_cache_key(client_id, normalized_query, knowledge_version)
    now = datetime.datetime.now(datetime.timezone.utc)
    return db.query(models.RagResponseCache).filter(
        models.RagResponseCache.client_id == client_id,
        models.RagResponseCache.cache_key == cache_key,
        models.RagResponseCache.expires_at > now,
    ).first()


def store_cached_answer(
    db: Session,
    client_id: uuid.UUID,
    normalized_query: str,
    knowledge_version: str,
    answer_text: str,
    confidence: float,
    sources: list[Dict[str, Any]],
    ttl_minutes: int,
) -> models.RagResponseCache:
    cache_key = make_cache_key(client_id, normalized_query, knowledge_version)
    expires_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=ttl_minutes)
    existing = db.query(models.RagResponseCache).filter(
        models.RagResponseCache.client_id == client_id,
        models.RagResponseCache.cache_key == cache_key,
    ).first()
    if existing:
        existing.answer_text = answer_text
        existing.confidence = confidence
        existing.sources = sources
        existing.expires_at = expires_at
        db.commit()
        db.refresh(existing)
        return existing

    entry = models.RagResponseCache(
        client_id=client_id,
        cache_key=cache_key,
        normalized_query=normalized_query,
        knowledge_version=knowledge_version,
        answer_text=answer_text,
        confidence=confidence,
        sources=sources,
        expires_at=expires_at,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
