import uuid
from typing import Any, Dict

from sqlalchemy.orm import Session

from app.storage import models


def log_source_trace(
    db: Session,
    client_id: uuid.UUID,
    conversation_id: uuid.UUID,
    message_id: uuid.UUID,
    normalized_query: str,
    confidence: float,
    knowledge_version: str,
    sources: list[Dict[str, Any]],
    answer_type: str = "rag_answer",
    metadata: Dict[str, Any] | None = None,
) -> models.RagSourceTrace:
    trace = models.RagSourceTrace(
        client_id=client_id,
        conversation_id=conversation_id,
        message_id=message_id,
        answer_type=answer_type,
        normalized_query=normalized_query,
        confidence=confidence,
        knowledge_version=knowledge_version,
        sources=sources,
        meta_data=metadata or {},
    )
    db.add(trace)
    db.commit()
    db.refresh(trace)
    return trace
