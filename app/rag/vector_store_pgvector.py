import uuid
from typing import List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.storage import models


def _to_pgvector_literal(values: List[float]) -> str:
    return "[" + ",".join(f"{float(value):.12g}" for value in values) + "]"

def search_similarity(
    db: Session,
    client_id: uuid.UUID,
    query_embedding: List[float],
    top_k: int = 5
) -> List[Tuple[models.KnowledgeChunk, float]]:
    """
    Performs cosine similarity search using pgvector in PostgreSQL.
    Returns a list of tuples containing (KnowledgeChunk, score).
    """
    if db.bind and db.bind.dialect.name != "postgresql":
        return []

    try:
        query_vector = _to_pgvector_literal(query_embedding)
        rows = db.execute(
            text(
                """
                SELECT
                    kc.id,
                    1 - (kc.embedding <=> CAST(:query_embedding AS vector)) AS score
                FROM knowledge_chunks kc
                JOIN knowledge_documents kd ON kc.document_id = kd.id
                WHERE kc.client_id = CAST(:client_id AS uuid)
                  AND kd.is_active = TRUE
                  AND kc.embedding IS NOT NULL
                ORDER BY kc.embedding <=> CAST(:query_embedding AS vector)
                LIMIT :top_k
                """
            ),
            {
                "client_id": str(client_id),
                "query_embedding": query_vector,
                "top_k": int(top_k),
            },
        ).mappings().all()

        chunk_ids = [row["id"] for row in rows]
        if not chunk_ids:
            return []

        chunks = db.query(models.KnowledgeChunk).filter(models.KnowledgeChunk.id.in_(chunk_ids)).all()
        chunks_by_id = {str(chunk.id): chunk for chunk in chunks}
        return [
            (chunks_by_id[str(row["id"])], float(row["score"]))
            for row in rows
            if str(row["id"]) in chunks_by_id
        ]
    except Exception as e:
        print(f"pgvector search_similarity failed (fallback to text-based mock if database has no pgvector extension): {str(e)}")
        # Return empty list in development if postgres doesn't support vector distance
        return []

def search_fulltext(
    db: Session,
    client_id: uuid.UUID,
    query_text: str,
    top_k: int = 15,
) -> List[Tuple[models.KnowledgeChunk, float]]:
    """
    PostgreSQL full-text search using tsvector + ts_rank.
    Uses 'indonesian' text search configuration.
    Returns list of (KnowledgeChunk, score) sorted by relevance.
    Falls back to empty list if not on PostgreSQL or if search fails.
    """
    if db.bind and db.bind.dialect.name != "postgresql":
        return []

    try:
        rows = db.execute(
            text(
                """
                SELECT
                    kc.id,
                    ts_rank(kc.search_vector, plainto_tsquery('indonesian', :query)) AS score
                FROM knowledge_chunks kc
                JOIN knowledge_documents kd ON kc.document_id = kd.id
                WHERE kc.client_id = CAST(:client_id AS uuid)
                  AND kd.is_active = TRUE
                  AND kc.search_vector IS NOT NULL
                  AND kc.search_vector @@ plainto_tsquery('indonesian', :query)
                ORDER BY score DESC
                LIMIT :top_k
                """
            ),
            {
                "client_id": str(client_id),
                "query": query_text,
                "top_k": int(top_k),
            },
        ).mappings().all()

        if not rows:
            return []

        chunk_ids = [row["id"] for row in rows]
        chunks = db.query(models.KnowledgeChunk).filter(
            models.KnowledgeChunk.id.in_(chunk_ids)
        ).all()
        chunks_by_id = {str(chunk.id): chunk for chunk in chunks}

        return [
            (chunks_by_id[str(row["id"])], float(row["score"]))
            for row in rows
            if str(row["id"]) in chunks_by_id
        ]
    except Exception as e:
        print(f"pgvector search_fulltext failed: {str(e)}")
        return []


def store_chunk(
    db: Session,
    client_id: uuid.UUID,
    document_id: uuid.UUID,
    chunk_index: int,
    chunk_text: str,
    embedding: List[float],
    metadata: dict = None
) -> models.KnowledgeChunk:
    """
    Inserts a knowledge chunk with vector embedding.
    """
    chunk = models.KnowledgeChunk(
        client_id=client_id,
        document_id=document_id,
        chunk_index=chunk_index,
        chunk_text=chunk_text,
        embedding=embedding,
        meta_data=metadata or {},
        token_count=len(chunk_text.split()) # Rough fallback estimate
    )
    db.add(chunk)
    db.commit()
    db.refresh(chunk)
    return chunk
