import datetime
import hashlib
import re
import uuid
from dataclasses import dataclass
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

MONTH_ALIASES = {
    "januari": "01",
    "februari": "02",
    "maret": "03",
    "april": "04",
    "mei": "05",
    "juni": "06",
    "juli": "07",
    "agustus": "08",
    "september": "09",
    "oktober": "10",
    "november": "11",
    "desember": "12",
}


@dataclass
class RetrievedChunk:
    chunk: models.KnowledgeChunk
    score: float
    rank_score: float
    retrieval_method: str


@dataclass
class RAGRetrievalResult:
    chunks: List[RetrievedChunk]
    confidence: float
    requires_handover: bool
    handover_reason: str
    normalized_query: str
    knowledge_version: str
    sources: List[Dict[str, Any]]


def normalize_query(text: str) -> str:
    cleaned = text.lower().strip()
    cleaned = re.sub(r"[^a-z0-9\s./+-]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def extract_structured_filters(text: str) -> Dict[str, Any]:
    normalized = normalize_query(text)
    package_codes = re.findall(r"\b[a-z]{2,}\d{1,3}[a-z0-9]*\b", normalized)
    cities = []
    for city in ("makkah", "madinah", "jeddah"):
        if city in normalized:
            cities.append(city)
    months = [number for name, number in MONTH_ALIASES.items() if name in normalized]
    document_type = ""
    if any(term in normalized for term in ("harga", "biaya", "tarif", "paket", "jadwal", "berangkat")):
        document_type = "package"
    elif any(term in normalized for term in ("hotel", "makkah", "madinah")):
        document_type = "hotel"
    elif any(term in normalized for term in ("maskapai", "airline", "pesawat", "bagasi")):
        document_type = "airline"
    elif any(term in normalized for term in ("refund", "pembatalan", "reschedule", "dp", "pelunasan")):
        document_type = "terms"
    return {
        "normalized_query": normalized,
        "package_codes": package_codes,
        "cities": cities,
        "months": months,
        "document_type": document_type,
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
        result = self.retrieve(
            query_text="",
            query_embedding=query_embedding,
            min_threshold=min_threshold,
            top_k=top_k,
        )
        return [(item.chunk, item.score) for item in result.chunks], result.requires_handover

    def retrieve(
        self,
        query_text: str,
        query_embedding: Optional[List[float]] = None,
        min_threshold: float = 0.55,
        top_k: int = 5,
        conversation_id: Optional[uuid.UUID] = None,
    ) -> RAGRetrievalResult:
        filters = extract_structured_filters(query_text)
        normalized_query = filters["normalized_query"]
        knowledge_version = self._knowledge_version()

        merged: Dict[str, Tuple[models.KnowledgeChunk, float, str]] = {}
        if query_embedding:
            for chunk, score in search_similarity(self.db, self.client_id, query_embedding, top_k=top_k * 3):
                merged[str(chunk.id)] = (chunk, score, "vector")

        for chunk, score in self._text_search(normalized_query, filters, top_k=top_k * 3):
            key = str(chunk.id)
            if key in merged:
                prev_chunk, prev_score, prev_method = merged[key]
                merged[key] = (prev_chunk, max(prev_score, score), f"{prev_method}+text")
            else:
                merged[key] = (chunk, score, "text")

        filtered_results = [
            (chunk, score)
            for chunk, score, _method in merged.values()
            if score >= min_threshold
        ]
        resolved_results = resolve_knowledge_conflicts(filtered_results)
        ranked = [
            RetrievedChunk(
                chunk=chunk,
                score=float(score),
                rank_score=self._rank_score(chunk, float(score), filters),
                retrieval_method=merged[str(chunk.id)][2],
            )
            for chunk, score in resolved_results
        ]
        ranked.sort(key=lambda item: item.rank_score, reverse=True)
        ranked = ranked[:top_k]

        confidence = ranked[0].rank_score if ranked else 0.0
        conflict_fields = self._conflict_fields([item.chunk for item in ranked])
        requires_handover = False
        handover_reason = ""
        if conflict_fields:
            requires_handover = self._conflict_requires_handover([item.chunk for item in ranked], conflict_fields)
            handover_reason = "knowledge_conflict" if requires_handover else "knowledge_conflict_resolved"
            self._log_conflict(conversation_id, query_text, conflict_fields, ranked, handover_reason)
        elif confidence < min_threshold:
            requires_handover = True
            handover_reason = "low_rag_confidence"

        return RAGRetrievalResult(
            chunks=ranked,
            confidence=confidence,
            requires_handover=requires_handover,
            handover_reason=handover_reason,
            normalized_query=normalized_query,
            knowledge_version=knowledge_version,
            sources=[self._source_dict(item) for item in ranked],
        )

    def _text_search(
        self,
        normalized_query: str,
        filters: Dict[str, Any],
        top_k: int,
    ) -> List[Tuple[models.KnowledgeChunk, float]]:
        if not normalized_query:
            return []
        terms = [term for term in normalized_query.split() if len(term) >= 3]
        if not terms:
            return []

        candidates = self.db.query(models.KnowledgeChunk).join(
            models.KnowledgeDocument,
            models.KnowledgeChunk.document_id == models.KnowledgeDocument.id,
        ).filter(
            models.KnowledgeChunk.client_id == self.client_id,
            models.KnowledgeDocument.is_active == True,
        ).limit(max(top_k * 5, 25)).all()

        scored = []
        for chunk in candidates:
            haystack = normalize_query(" ".join([
                chunk.chunk_text or "",
                str(chunk.meta_data or {}),
            ]))
            hits = sum(1 for term in terms if term in haystack)
            if hits == 0:
                continue
            score = min(0.45 + (hits / max(len(terms), 1)) * 0.35, 0.80)
            meta = chunk.meta_data or {}
            if filters.get("document_type") and filters["document_type"] in str(meta.get("document_type", meta.get("source_type", ""))).lower():
                score += 0.05
            if any(code in haystack for code in filters.get("package_codes", [])):
                score += 0.08
            if any(city in haystack for city in filters.get("cities", [])):
                score += 0.06
            scored.append((chunk, min(score, 0.92)))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]

    def _rank_score(self, chunk: models.KnowledgeChunk, score: float, filters: Dict[str, Any]) -> float:
        meta = chunk.meta_data or {}
        source_rank = SOURCE_PRIORITY.get(meta.get("source_type", "faq"), 1) / 100
        priority_boost = min(int(meta.get("doc_priority", 50)), 100) / 1000
        haystack = normalize_query(" ".join([chunk.chunk_text or "", str(meta)]))
        structured_boost = 0.0
        if any(code in haystack for code in filters.get("package_codes", [])):
            structured_boost += 0.06
        if any(city in haystack for city in filters.get("cities", [])):
            structured_boost += 0.04
        if any(month in haystack for month in filters.get("months", [])):
            structured_boost += 0.03
        return min(score + source_rank + priority_boost + structured_boost, 1.0)

    def _knowledge_version(self) -> str:
        docs = self.db.query(models.KnowledgeDocument).filter(
            models.KnowledgeDocument.client_id == self.client_id,
            models.KnowledgeDocument.is_active == True,
        ).all()
        if not docs:
            return "empty"
        parts = []
        for doc in docs:
            updated = doc.updated_at.isoformat() if doc.updated_at else ""
            parts.append(f"{doc.doc_version}:{updated}")
        raw = "|".join(sorted(parts))
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]

    def _source_dict(self, item: RetrievedChunk) -> Dict[str, Any]:
        meta = item.chunk.meta_data or {}
        return {
            "document_id": str(item.chunk.document_id),
            "chunk_id": str(item.chunk.id),
            "document_type": meta.get("document_type") or meta.get("source_type", ""),
            "knowledge_version": meta.get("doc_version", ""),
            "similarity_score": round(item.score, 4),
            "rank_score": round(item.rank_score, 4),
            "retrieval_method": item.retrieval_method,
        }

    def _conflict_fields(self, chunks: List[models.KnowledgeChunk]) -> List[str]:
        fields = []
        for field in ("price_per_pax", "price", "departure_date", "bank_account", "remaining_seat", "quota"):
            values = {str((chunk.meta_data or {}).get(field)) for chunk in chunks if (chunk.meta_data or {}).get(field) is not None}
            if len(values) > 1:
                fields.append(field)
        return fields

    def _conflict_requires_handover(self, chunks: List[models.KnowledgeChunk], conflict_fields: List[str]) -> bool:
        if len(chunks) < 2:
            return False
        first = self._conflict_strength(chunks[0])
        for chunk in chunks[1:]:
            if self._conflict_strength(chunk) == first:
                for field in conflict_fields:
                    if (chunk.meta_data or {}).get(field) != (chunks[0].meta_data or {}).get(field):
                        return True
        return False

    def _conflict_strength(self, chunk: models.KnowledgeChunk) -> Tuple[Any, ...]:
        meta = chunk.meta_data or {}
        return (
            SOURCE_PRIORITY.get(meta.get("source_type", "faq"), 0),
            int(meta.get("doc_priority", 50)),
            parse_version(str(meta.get("doc_version", "1"))),
            str(meta.get("updated_at", "")),
        )

    def _log_conflict(
        self,
        conversation_id: Optional[uuid.UUID],
        query_text: str,
        fields: List[str],
        ranked: List[RetrievedChunk],
        resolution: str,
    ) -> None:
        log = models.KnowledgeConflictLog(
            client_id=self.client_id,
            conversation_id=conversation_id,
            query_text=query_text,
            conflict_type="transactional_field_conflict",
            conflict_fields=fields,
            sources=[self._source_dict(item) for item in ranked],
            resolution=resolution,
        )
        self.db.add(log)
        self.db.commit()
