import datetime
import uuid

from app.rag.ingestion_service import ingest_text_knowledge, list_knowledge_documents
from app.rag.retriever import RAGRetriever
from app.storage import models


def test_ingest_text_knowledge_creates_chunks_and_invalidates_cache(test_db):
    client_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    test_db.add(models.RagResponseCache(
        client_id=client_id,
        cache_key="old",
        normalized_query="old",
        knowledge_version="old",
        answer_text="old",
        confidence=0.9,
        sources=[],
        expires_at=datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=1),
    ))
    test_db.commit()

    result = ingest_text_knowledge(
        test_db,
        client_id=client_id,
        client_code="travel_alfalah",
        title="Itinerary Makkah Dulu",
        text=(
            "Paket request Makkah dulu boleh jika admin menyetujui dan seat tersedia. "
            "Urutan perjalanan dapat dimulai dari Makkah lalu Madinah untuk grup tertentu."
        ),
        source_type="admin_override",
        document_type="itinerary",
    )

    assert result["chunk_count"] >= 1
    assert test_db.query(models.KnowledgeDocument).filter_by(client_id=client_id).count() == 1
    assert test_db.query(models.KnowledgeChunk).filter_by(client_id=client_id).count() >= 1
    assert test_db.query(models.RagResponseCache).filter_by(client_id=client_id).count() == 0

    docs = list_knowledge_documents(test_db, client_id)
    assert docs[0]["title"] == "Itinerary Makkah Dulu"
    assert docs[0]["document_type"] == "itinerary"


def test_ingested_knowledge_is_retrievable(test_db):
    client_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    ingest_text_knowledge(
        test_db,
        client_id=client_id,
        client_code="travel_alfalah",
        title="Hotel Madinah",
        text="Hotel Madinah untuk paket reguler adalah Hotel Daus Madinah, jarak sekitar 300 meter dari Masjid Nabawi.",
        source_type="admin_override",
        document_type="hotel",
    )

    result = RAGRetriever(test_db, client_id).retrieve("hotel madinah apa?", top_k=3)

    assert result.chunks
    assert "Hotel Daus Madinah" in result.chunks[0].chunk.chunk_text
