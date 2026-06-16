import re
from typing import Dict, Any, Tuple, Optional
from app.ai.llm_client import LLMClient

# Basic regex for rule-based intent mapping (Rule-Based Intent First)
RULES_INTENTS = [
    (r"\b(admin|cs|manusia|operator|hubungi)\b", "human_request"),
    (r"\b(ok|oke|siap|baik|makasih|terima kasih|suwun)\b", "acknowledgement"),
    (r"\b(booking|daftar|pesan|ambil seat)\b", "booking_intent"),
    (r"\b(harga|tarif|biaya|berapa|ongkos)\b", "ask_price"),
    (r"\b(paket|umroh|jadwal|tanggal|keberangkatan)\b", "ask_package"),
    (r"\b(bukti|bayar|transfer|tf|resi|lunas)\b", "payment_evidence")
]

def check_rule_based_intent(text: str) -> Optional[str]:
    cleaned = text.lower().strip()
    for pattern, intent in RULES_INTENTS:
        if re.search(pattern, cleaned):
            return intent
    return None

class IntentDetector:
    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.llm_client = llm_client

    def detect_intent(self, text: str) -> Tuple[str, float]:
        """
        Detects user intent from text. Returns a tuple (intent_name, confidence).
        First checks regex rules, then falls back to LLM if rules are inconclusive.
        """
        # Rule-based check first
        rule_intent = check_rule_based_intent(text)
        if rule_intent:
            return rule_intent, 1.0

        if not self.llm_client:
            return "unknown", 0.5

        # LLM fallback
        prompt = """
        Analyze the user's intent. Output exactly one of the following labels:
        - ask_package: asking about package details, itineraries, hotels, flights.
        - ask_price: asking about prices, costs, payments.
        - booking_intent: indicating a desire to register or reserve a package.
        - payment_evidence: sending or talking about transaction verification.
        - human_request: asking to talk to a human or CS agent.
        - complaint: expressing anger, frustration, or objections.
        - unknown: none of the above.
        
        Output only the label, nothing else.
        """
        try:
            response = self.llm_client.generate_chat_response(
                messages=[{"role": "user", "content": text}],
                system_prompt=prompt,
                temperature=0.0,
                max_tokens=15
            )
            detected = response.strip().lower()
            valid_intents = {"ask_package", "ask_price", "booking_intent", "payment_evidence", "human_request", "complaint"}
            if detected in valid_intents:
                return detected, 0.9
            return "unknown", 0.7
        except Exception:
            return "unknown", 0.5
