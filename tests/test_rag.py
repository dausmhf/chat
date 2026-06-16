import datetime
import uuid
from app.conversation.orchestrator import process_incoming_message
from app.rag.cache_manager import get_cached_answer, store_cached_answer
from app.rag.retriever import RAGRetriever, resolve_knowledge_conflicts, parse_version
from app.storage import models

def test_conflict_resolution_sorting():
    # Setup knowledge chunks with different priority details in meta_data
    chunk_faq = models.KnowledgeChunk(
        id=uuid.uuid4(),
        chunk_text="Detail dari FAQ",
        meta_data={
            "source_type": "faq",
            "doc_priority": 50,
            "doc_version": "1.0.0",
            "updated_at": "2026-01-01T00:00:00Z"
        }
    )
    chunk_db = models.KnowledgeChunk(
        id=uuid.uuid4(),
        chunk_text="Detail dari Package DB",
        meta_data={
            "source_type": "package_database",
            "doc_priority": 80,
            "doc_version": "2.0.0",
            "updated_at": "2026-02-01T00:00:00Z"
        }
    )
    chunk_override = models.KnowledgeChunk(
        id=uuid.uuid4(),
        chunk_text="Detail dari Admin Override",
        meta_data={
            "source_type": "admin_override",
            "doc_priority": 90,
            "doc_version": "1.5.0",
            "updated_at": "2026-03-01T00:00:00Z"
        }
    )

    results = [(chunk_faq, 0.9), (chunk_db, 0.8), (chunk_override, 0.85)]
    resolved = resolve_knowledge_conflicts(results)
    
    # Expected ordering: package_database first, then admin_override, then faq
    assert len(resolved) == 3
    assert resolved[0][0].meta_data["source_type"] == "package_database"
    assert resolved[1][0].meta_data["source_type"] == "admin_override"
    assert resolved[2][0].meta_data["source_type"] == "faq"

def test_parse_version():
    assert parse_version("v1.2.3") == [1, 2, 3]
    assert parse_version("2") == [2]
    assert parse_version("invalid") == [0]


def test_hybrid_retriever_uses_text_search_without_vector(test_db):
    client_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    doc = models.KnowledgeDocument(
        client_id=client_id,
        title="Paket Oktober",
        source_type="package_database",
        doc_version="2026.06.16.001",
        doc_priority=90,
        is_active=True,
    )
    test_db.add(doc)
    test_db.commit()
    test_db.refresh(doc)
    chunk = models.KnowledgeChunk(
        client_id=client_id,
        document_id=doc.id,
        chunk_index=0,
        chunk_text="Paket OCT12D Oktober harga Rp29.000.000 berangkat Oktober.",
        meta_data={
            "source_type": "package_database",
            "document_type": "package",
            "doc_priority": 90,
            "doc_version": "2026.06.16.001",
            "price_per_pax": 29000000,
        },
    )
    test_db.add(chunk)
    test_db.commit()

    result = RAGRetriever(test_db, client_id).retrieve("Berapa harga paket OCT12D Oktober?", top_k=3)

    assert result.chunks
    assert result.confidence >= 0.55
    assert result.sources[0]["retrieval_method"] == "text"


def test_retriever_logs_unresolved_conflict(test_db):
    client_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    doc = models.KnowledgeDocument(
        client_id=client_id,
        title="Harga Konflik",
        source_type="package_database",
        doc_version="1",
        doc_priority=80,
        is_active=True,
    )
    test_db.add(doc)
    test_db.commit()
    test_db.refresh(doc)
    for idx, price in enumerate([29000000, 31000000]):
        test_db.add(models.KnowledgeChunk(
            client_id=client_id,
            document_id=doc.id,
            chunk_index=idx,
            chunk_text=f"Paket Oktober harga Rp{price} untuk OCT12D.",
            meta_data={
                "source_type": "package_database",
                "document_type": "package",
                "doc_priority": 80,
                "doc_version": "1",
                "price_per_pax": price,
                "updated_at": "2026-06-16T00:00:00Z",
            },
        ))
    test_db.commit()

    result = RAGRetriever(test_db, client_id).retrieve("harga paket oktober", min_threshold=0.1, top_k=5)

    assert result.requires_handover is True
    assert result.handover_reason == "knowledge_conflict"
    assert test_db.query(models.KnowledgeConflictLog).filter_by(client_id=client_id).count() == 1


def test_rag_cache_roundtrip(test_db):
    client_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    store_cached_answer(
        test_db,
        client_id=client_id,
        normalized_query="harga paket oktober",
        knowledge_version="v1",
        answer_text="Harga resmi Rp29.000.000.",
        confidence=0.88,
        sources=[{"chunk_id": "chunk_1"}],
        ttl_minutes=60,
    )

    cached = get_cached_answer(test_db, client_id, "harga paket oktober", "v1")

    assert cached is not None
    assert cached.answer_text == "Harga resmi Rp29.000.000."


def test_orchestrator_logs_rag_source_trace(test_db):
    client_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    doc = models.KnowledgeDocument(
        client_id=client_id,
        title="Paket Trace",
        source_type="package_database",
        doc_version="trace-v1",
        doc_priority=95,
        is_active=True,
    )
    test_db.add(doc)
    test_db.commit()
    test_db.refresh(doc)
    test_db.add(models.KnowledgeChunk(
        client_id=client_id,
        document_id=doc.id,
        chunk_index=0,
        chunk_text="Paket OCT12D harga Rp29.000.000 untuk 12 hari.",
        meta_data={
            "source_type": "package_database",
            "document_type": "package",
            "doc_priority": 95,
            "doc_version": "trace-v1",
            "price_per_pax": 29000000,
        },
    ))
    test_db.commit()

    result = process_incoming_message(test_db, "starsender", {
        "messageId": "rag_trace_1",
        "message": "Berapa harga paket OCT12D?",
        "phone": "628222222222",
        "name": "Rag Trace",
        "client_id": "travel_alfalah",
    }, send_reply=False)

    assert result["status"] == "replied"
    trace = test_db.query(models.RagSourceTrace).filter_by(client_id=client_id).first()
    assert trace is not None
    assert trace.sources
