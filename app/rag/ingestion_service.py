import datetime
import re
import uuid
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.ai.llm_client import LLMClient
from app.config import client_config_manager
from app.storage import models


def ingest_text_knowledge(
    db: Session,
    *,
    client_id: uuid.UUID,
    client_code: str,
    title: str,
    text: str,
    source_type: str = "admin_override",
    document_type: str = "faq",
    doc_priority: int = 70,
    metadata: Optional[Dict] = None,
) -> Dict[str, object]:
    clean_text = _normalize_text(text)
    if len(clean_text) < 20:
        raise ValueError("Knowledge terlalu pendek. Minimal 20 karakter.")

    configs = client_config_manager.load_all_configs(client_code)
    llm_client = LLMClient(client_code, configs["ai"])
    doc_version = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")

    document = models.KnowledgeDocument(
        client_id=client_id,
        title=title.strip()[:220],
        source_type=source_type,
        doc_version=doc_version,
        doc_priority=max(1, min(int(doc_priority), 100)),
        is_active=True,
        meta_data={
            "document_type": document_type,
            **(metadata or {}),
        },
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    chunks = _chunk_text(clean_text)
    for index, chunk_text in enumerate(chunks):
        embedding = llm_client.embed_text(chunk_text)
        db.add(models.KnowledgeChunk(
            client_id=client_id,
            document_id=document.id,
            chunk_index=index,
            chunk_text=chunk_text,
            embedding=embedding,
            token_count=len(chunk_text.split()),
            meta_data={
                "source_type": source_type,
                "document_type": document_type,
                "doc_priority": document.doc_priority,
                "doc_version": doc_version,
                **(metadata or {}),
            },
        ))

    db.query(models.RagResponseCache).filter(models.RagResponseCache.client_id == client_id).delete()
    db.add(models.AuditLog(
        client_id=client_id,
        actor_type="admin",
        event_type="knowledge_ingested",
        entity_type="knowledge_documents",
        entity_id=document.id,
        new_value={
            "title": document.title,
            "source_type": source_type,
            "document_type": document_type,
            "chunk_count": len(chunks),
            "doc_version": doc_version,
        },
    ))
    db.commit()
    return {
        "document_id": str(document.id),
        "title": document.title,
        "doc_version": doc_version,
        "chunk_count": len(chunks),
        "cache_invalidated": True,
    }


def list_knowledge_documents(db: Session, client_id: uuid.UUID, limit: int = 50) -> List[Dict[str, object]]:
    rows = (
        db.query(models.KnowledgeDocument)
        .filter(models.KnowledgeDocument.client_id == client_id)
        .order_by(models.KnowledgeDocument.created_at.desc())
        .limit(min(max(limit, 1), 100))
        .all()
    )
    result = []
    for doc in rows:
        chunk_count = (
            db.query(models.KnowledgeChunk)
            .filter(models.KnowledgeChunk.client_id == client_id, models.KnowledgeChunk.document_id == doc.id)
            .count()
        )
        result.append({
            "id": str(doc.id),
            "title": doc.title,
            "source_type": doc.source_type,
            "document_type": (doc.meta_data or {}).get("document_type", ""),
            "doc_version": doc.doc_version,
            "doc_priority": doc.doc_priority,
            "is_active": doc.is_active,
            "chunk_count": chunk_count,
            "created_at": doc.created_at.isoformat() if doc.created_at else "",
        })
    return result


def _normalize_text(text: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", text.strip())


def _chunk_text(text: str, max_words: int = 180, overlap_words: int = 30) -> List[str]:
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: List[str] = []
    current: List[str] = []
    for paragraph in paragraphs:
        words = paragraph.split()
        if len(current) + len(words) <= max_words:
            current.extend(words)
            continue
        if current:
            chunks.append(" ".join(current))
        if len(words) > max_words:
            start = 0
            while start < len(words):
                chunks.append(" ".join(words[start:start + max_words]))
                start += max_words - overlap_words
            current = []
        else:
            current = words
    if current:
        chunks.append(" ".join(current))
    return chunks or [text]
