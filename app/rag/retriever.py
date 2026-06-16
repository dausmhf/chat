import datetime
import uuid
from typing import List, Dict, Any, Tuple, Optional
from sqlalchemy.orm import Session
from app.storage import models
from app.rag.vector_store_pgvector import search_similarity

SOURCE_PRIORITY = {
    "package_database": 6,
    "admin_override": 5,
    "price_sheet": 4,
    "brochure_pdf": 3,
    "faq": 2,
    "old_chat_import": 1
}

def parse_version(version_str: str) -> List[int]:
    """
    Parses a semver or simple integer string to list of ints for comparison.
    """
    cleaned = version_str.replace("v", "").replace("-", ".").split(".")
    parsed = []
    for part in cleaned:
        try:
            parsed.append(int(part))
        except ValueError:
            parsed.append(0)
    return parsed

def resolve_knowledge_conflicts(chunks_with_scores: List[Tuple[models.KnowledgeChunk, float]]) -> List[Tuple[models.KnowledgeChunk, float]]:
    """
    Filters and resolves conflicts between retrieved chunks using the deterministic priority rules:
    1. Active date validation: must be active (is_active=True and effective_from <= now <= effective_until if set).
    2. Source Type ranking: package_database > admin_override > price_sheet > brochure_pdf > faq > old_chat_import.
    3. doc_priority: range 1-100 (higher is stronger).
    4. doc_version: higher version is stronger.
    5. updated_at: newer updated time is stronger.
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    valid_chunks = []

    for chunk, score in chunks_with_scores:
        doc = chunk.meta_data.get("document_ref")
        # If document_ref is not present in metadata, check chunk's relation models if preloaded
        # For simplicity, doc is usually preloaded or loaded from database inside context.
        # Let's verify status checks
        is_active = chunk.meta_data.get("is_active", True)
        if not is_active:
            continue
            
        # Check active date validation
        eff_from = chunk.meta_data.get("effective_from")
        eff_until = chunk.meta_data.get("effective_until")
        
        if eff_from:
            try:
                dt_from = datetime.datetime.fromisoformat(eff_from)
                if dt_from > now:
                    continue
            except ValueError:
                pass
        if eff_until:
            try:
                dt_until = datetime.datetime.fromisoformat(eff_until)
                if dt_until < now:
                    continue
            except ValueError:
                pass
                
        valid_chunks.append((chunk, score))

    if not valid_chunks:
        return []

    # Sort chunks by conflict resolution rules
    def get_sort_key(item: Tuple[models.KnowledgeChunk, float]):
        chunk, score = item
        # Read properties from metadata or document
        src_type = chunk.meta_data.get("source_type", "faq")
        src_rank = SOURCE_PRIORITY.get(src_type, 0)
        
        priority = int(chunk.meta_data.get("doc_priority", 50))
        
        version_str = str(chunk.meta_data.get("doc_version", "1"))
        version_key = parse_version(version_str)
        
        updated_at_str = chunk.meta_data.get("updated_at", "")
        
        # We want higher rank first, so we reverse ordering in sort or use negative ints
        return (src_rank, priority, version_key, updated_at_str, score)

    # Sort in descending order of strength (using reverse=True)
    valid_chunks.sort(key=get_sort_key, reverse=True)
    return valid_chunks

class RAGRetriever:
    def __init__(self, db: Session, client_id: uuid.UUID):
        self.db = db
        self.client_id = client_id

    def retrieve_context(
        self,
        query_embedding: List[float],
        min_threshold: float = 0.55,
        top_k: int = 5
    ) -> Tuple[List[models.KnowledgeChunk], bool]:
        """
        Retrieves matching chunks, filters by score threshold, resolves conflicts.
        Returns:
            (list_of_chunks, requires_handover)
            If conflicts exist in transaksional data (prices, bank account, date) and cannot be resolved,
            requires_handover is set to True.
        """
        raw_results = search_similarity(self.db, self.client_id, query_embedding, top_k=top_k)
        
        # Filter by threshold
        filtered_results = [(chunk, score) for chunk, score in raw_results if score >= min_threshold]
        
        # Resolve conflicts
        resolved_results = resolve_knowledge_conflicts(filtered_results)
        
        # Detect if we have conflicts in transaction fields
        requires_handover = False
        trans_fields = {"price", "price_per_pax", "departure_date", "bank_account", "remaining_seat", "quota"}
        
        # Simple conflict check: if we have multiple active documents of different source types giving different details
        # for transactional properties, we set handover flag.
        resolved_chunks = [item[0] for item in resolved_results]
        
        # Inspect for values conflicts in critical fields
        seen_prices = set()
        seen_dates = set()
        for chunk in resolved_chunks:
            meta = chunk.meta_data
            price = meta.get("price_per_pax") or meta.get("price")
            date = meta.get("departure_date")
            if price:
                seen_prices.add(price)
            if date:
                seen_dates.add(date)
                
        if len(seen_prices) > 1 or len(seen_dates) > 1:
            # Ambiguous transactional information
            requires_handover = True
            print("RAGRetriever: Conflict detected in transactional data (prices/dates). Handover required.")

        return resolved_chunks, requires_handover
