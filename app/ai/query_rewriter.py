"""
Query Rewriter for RAG — expands abbreviations, normalizes variations,
and injects multi-turn conversation context before retrieval.
"""

import re
from typing import List, Tuple

# --- Abbreviation expansion ---
ABBREVIATION_MAP = {
    "okt": "oktober",
    "nov": "november",
    "des": "desember",
    "jan": "januari",
    "feb": "februari",
    "mar": "maret",
    "apr": "april",
    "mei": "mei",
    "jun": "juni",
    "jul": "juli",
    "agu": "agustus",
    "sep": "september",
    "jt": "juta",
    "rb": "ribu",
}

# --- Normalization: variant → canonical ---
NORMALIZATION_MAP = {
    "mekkah": "makkah",
    "harganya": "harga",
    "biayanya": "biaya",
    "jadwalnya": "jadwal",
    "paketnya": "paket",
    "berangkatan": "keberangkatan",
    "pesan": "booking",
    "daftar": "booking",
    "mau tanya": "",
    "saya mau tanya": "",
}

# --- Keywords that suggest the query is incomplete without history ---
REFERRING_PHRASES = [
    r"\bitu\b", r"\bini\b", r"\btadi\b", r"\bsebelumnya\b",
    r"\byang (itu|ini|tadi)\b", r"\bberapaan\b", r"\bgimana\b",
    r"\bberapa harganya\b", r"\bdetailnya\b",
]

# --- Key entities to extract from conversation history ---
ENTITY_PATTERNS = {
    "package_code": re.compile(r"\b([A-Z]{2,4}\d{1,3}[A-Z0-9]*)\b"),
    "month_ref": re.compile(
        r"\b(januari|februari|maret|april|mei|juni|juli|"
        r"agustus|september|oktober|november|desember|"
        r"okt|nov|des|jan|feb|mar|apr|mei|jun|jul|agu|sep)\b",
        re.IGNORECASE,
    ),
    "pax_ref": re.compile(r"(\d+)\s*(pax|orang)\b", re.IGNORECASE),
    "city_ref": re.compile(r"\b(makkah|madinah|jeddah|mekkah)\b", re.IGNORECASE),
}


def _expand_abbreviations(text: str) -> str:
    """Replace known abbreviations with full forms (word-boundary aware)."""
    words = text.split()
    result = []
    for word in words:
        lower = word.lower().strip(".,!?")
        if lower in ABBREVIATION_MAP:
            result.append(ABBREVIATION_MAP[lower])
        else:
            result.append(word)
    return " ".join(result)


def _normalize_variants(text: str) -> str:
    """Replace colloquial/dialect variants with canonical terms."""
    result = text
    for variant, canonical in NORMALIZATION_MAP.items():
        pattern = re.compile(r"\b" + re.escape(variant) + r"\b", re.IGNORECASE)
        result = pattern.sub(canonical, result)
    return result


def _is_referring_query(text: str) -> bool:
    """Check if query likely refers to previous conversation context."""
    lowered = text.lower().strip()
    for pattern in REFERRING_PHRASES:
        if re.search(pattern, lowered):
            return True
    return False


def _extract_history_entities(history_lines: List[str]) -> dict:
    """Extract key entities from recent conversation history."""
    entities = {
        "package_codes": [],
        "months": [],
        "pax": None,
        "cities": [],
    }
    for line in history_lines[-6:]:  # last 3 turns (user + bot = 6 messages)
        for match in ENTITY_PATTERNS["package_code"].finditer(line):
            code = match.group(0)
            if code not in entities["package_codes"]:
                entities["package_codes"].append(code)
        for match in ENTITY_PATTERNS["month_ref"].finditer(line):
            month = match.group(0).lower()
            # normalize abbreviation
            month = ABBREVIATION_MAP.get(month, month)
            if month not in entities["months"]:
                entities["months"].append(month)
        for match in ENTITY_PATTERNS["pax_ref"].finditer(line):
            entities["pax"] = int(match.group(1))
        for match in ENTITY_PATTERNS["city_ref"].finditer(line):
            city = match.group(0).lower()
            if city not in entities["cities"]:
                entities["cities"].append(city)
    return entities


def _build_context_suffix(entities: dict) -> str:
    """Build a context string from extracted history entities."""
    parts = []
    if entities.get("package_codes"):
        parts.append("paket " + " atau ".join(entities["package_codes"]))
    if entities.get("months"):
        parts.append("bulan " + " ".join(entities["months"]))
    if entities.get("cities"):
        parts.append("di " + " ".join(entities["cities"]))
    if parts:
        return "tentang " + " ".join(parts)
    return ""


def _extract_history_texts(history: List[dict]) -> List[str]:
    """Convert history dicts to plain text lines for entity extraction."""
    return [msg.get("content", "") for msg in history if msg.get("content")]


def rewrite_for_rag(
    query: str,
    history: List[dict],
) -> Tuple[str, str]:
    """
    Rewrites a user query for better RAG retrieval by:
    1. Expanding abbreviations (okt → oktober)
    2. Normalizing variants (mekkah → makkah)
    3. Injecting context from conversation history if query is ambiguous

    Returns:
        (expanded_query, original_query)
    """
    original = query.strip()
    if not original:
        return original, original

    # Step 1: expand abbreviations
    expanded = _expand_abbreviations(original)

    # Step 2: normalize variants
    expanded = _normalize_variants(expanded)

    # Step 3: check if query refers to previous context
    history_texts = _extract_history_texts(history)
    if history_texts and _is_referring_query(original):
        entities = _extract_history_entities(history_texts)
        context_suffix = _build_context_suffix(entities)
        if context_suffix and context_suffix not in expanded.lower():
            expanded = f"{expanded} {context_suffix}".strip()

    # Don't return identical results if nothing changed
    if expanded == original:
        return original, original

    return expanded, original
