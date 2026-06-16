import datetime
import re
import uuid
from io import BytesIO
from pathlib import Path
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from app.ai.llm_client import LLMClient
from app.config import client_config_manager, settings
from app.storage import models

MAX_KNOWLEDGE_FILE_BYTES = 10 * 1024 * 1024
SUPPORTED_KNOWLEDGE_EXTENSIONS = {".pdf", ".txt", ".md"}


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
    file_path: Optional[str] = None,
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
        file_path=file_path,
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


def ingest_file_knowledge(
    db: Session,
    *,
    client_id: uuid.UUID,
    client_code: str,
    title: str,
    filename: str,
    content: bytes,
    content_type: str = "",
    source_type: str = "brochure_pdf",
    document_type: str = "faq",
    doc_priority: int = 70,
    metadata: Optional[Dict] = None,
) -> Dict[str, object]:
    if not content:
        raise ValueError("File kosong.")
    if len(content) > MAX_KNOWLEDGE_FILE_BYTES:
        raise ValueError("File terlalu besar. Maksimal 10 MB.")

    ext = Path(filename or "").suffix.lower()
    if ext not in SUPPORTED_KNOWLEDGE_EXTENSIONS:
        raise ValueError("Format file belum didukung. Gunakan PDF, TXT, atau MD.")

    stored_path = _store_knowledge_file(client_code, filename, content)
    text = _extract_text_from_file(filename, content)
    if len(text.strip()) < 20:
        raise ValueError("Teks dari file terlalu pendek atau tidak terbaca.")

    file_metadata = {
        "original_filename": filename,
        "content_type": content_type,
        "stored_file_path": stored_path,
        **(metadata or {}),
    }
    return ingest_text_knowledge(
        db,
        client_id=client_id,
        client_code=client_code,
        title=title,
        text=text,
        source_type=source_type,
        document_type=document_type,
        doc_priority=doc_priority,
        metadata=file_metadata,
        file_path=stored_path,
    )


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


def _store_knowledge_file(client_code: str, filename: str, content: bytes) -> str:
    upload_dir = Path(settings.storage_root) / "knowledge_uploads" / client_code
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_filename(filename)
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M%S")
    path = upload_dir / f"{timestamp}_{safe_name}"
    path.write_bytes(content)
    return str(path)


def _extract_text_from_file(filename: str, content: bytes) -> str:
    ext = Path(filename or "").suffix.lower()
    if ext == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ValueError("PDF parser belum tersedia di environment.") from exc
        reader = PdfReader(BytesIO(content))
        return "\n\n".join((page.extract_text() or "").strip() for page in reader.pages)
    if ext in {".txt", ".md"}:
        return content.decode("utf-8", errors="replace")
    raise ValueError("Format file belum didukung. Gunakan PDF, TXT, atau MD.")


def _safe_filename(filename: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename or "knowledge.txt").name)
    return cleaned[:160] or "knowledge.txt"


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
