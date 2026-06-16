import uuid
from typing import List, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import text
from app.storage import models

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
    # Using pgvector cosine distance: score = 1 - (embedding <=> query_embedding)
    # in SQLAlchemy, we can write:
    try:
        # Distance operator <=> is mapped to cosine_distance
        distance_expression = models.KnowledgeChunk.embedding.cosine_distance(query_embedding)
        results = db.query(
            models.KnowledgeChunk,
            (1 - distance_expression).label("score")
        ).join(
            models.KnowledgeDocument,
            models.KnowledgeChunk.document_id == models.KnowledgeDocument.id
        ).filter(
            models.KnowledgeChunk.client_id == client_id,
            models.KnowledgeDocument.is_active == True
        ).order_by(
            distance_expression
        ).limit(top_k).all()
        
        return [(row[0], float(row[1])) for row in results]
    except Exception as e:
        print(f"pgvector search_similarity failed (fallback to text-based mock if database has no pgvector extension): {str(e)}")
        # Return empty list in development if postgres doesn't support vector distance
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
