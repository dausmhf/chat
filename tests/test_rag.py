import datetime
import uuid
from app.rag.retriever import resolve_knowledge_conflicts, parse_version
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
