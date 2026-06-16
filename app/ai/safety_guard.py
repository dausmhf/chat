import re
from typing import Tuple, Dict, Any, Optional

FORBIDDEN_CONFIRMATION_PATTERNS = [
    r"\bpembayaran\s+(berhasil|valid|diterima|lunas)\b",
    r"\bsudah\s+lunas\b",
    r"\blunas\s+ya\b",
    r"\btransfer\s+berhasil\b",
    r"\btransfer\s+valid\b",
    r"\buang\s+masuk\b"
]

TRANSACTIONAL_FACT_PATTERNS = [
    r"\b(hotel|maskapai|airline|tanggal|berangkat|jadwal|harga|tarif|biaya|seat|kuota|quota|rekening|refund|cancel|pembatalan)\b",
    r"\b\d{1,3}(?:[.,]\d{3}){2,}\b",
    r"\b\d+\s*(juta|jt|pax|orang|hari)\b",
]

FORBIDDEN_PROMISE_PATTERNS = [
    r"\bdijamin\s+berangkat\b",
    r"\bseat\s+pasti\s+aman\b",
    r"\bpasti\s+refund\b",
    r"\bpasti\s+disetujui\b",
    r"\btransfer\s+saja\s+ke\s+rekening\s+admin\b",
]

def check_forbidden_confirmations(text: str) -> bool:
    cleaned = text.lower()
    for pattern in FORBIDDEN_CONFIRMATION_PATTERNS:
        if re.search(pattern, cleaned):
            return True
    return False

def check_unregistered_bank_accounts(text: str, payment_config: Dict[str, Any]) -> bool:
    """
    Finds any numeric strings in response that look like bank accounts,
    and returns True if they do not match any active bank account number from config.
    """
    # Find all sequences of digits >= 7 characters (which look like account numbers)
    numbers = re.findall(r"\b\d{7,20}\b", text)
    if not numbers:
        return False
        
    # Get active bank accounts
    bank_accounts = payment_config.get("bank_accounts", [])
    active_numbers = {acc.get("account_number") for acc in bank_accounts if acc.get("is_active", True)}
    
    for num in numbers:
        if num not in active_numbers:
            # AI is mentioning a bank account not in config!
            print(f"SafetyGuard: Found unregistered bank account number '{num}' in AI output.")
            return True
            
    return False

def check_forbidden_promises(text: str) -> bool:
    cleaned = text.lower()
    return any(re.search(pattern, cleaned) for pattern in FORBIDDEN_PROMISE_PATTERNS)

def needs_official_context(text: str) -> bool:
    cleaned = text.lower()
    return any(re.search(pattern, cleaned) for pattern in TRANSACTIONAL_FACT_PATTERNS)

def validate_response(
    response_text: str,
    payment_config: Dict[str, Any],
    context_available: bool = True,
    intent: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Validates output text against policy rules.
    Returns:
        (is_safe, final_response)
    """
    # 1. Check forbidden confirmations
    if check_forbidden_confirmations(response_text):
        print("SafetyGuard: Response blocked due to payment confirmation violation.")
        fallback = payment_config.get(
            "verification_message",
            "Pembayaran akan diverifikasi manual oleh admin melalui mutasi rekening resmi."
        )
        return False, fallback

    if check_forbidden_promises(response_text):
        print("SafetyGuard: Response blocked due to forbidden operational promise.")
        return False, "Untuk memastikan informasinya aman dan sesuai ketentuan travel, saya bantu teruskan ke admin ya Ayah/Bunda."

    # 2. Check unregistered bank accounts
    if check_unregistered_bank_accounts(response_text, payment_config):
        print("SafetyGuard: Response blocked due to unregistered bank account reference.")
        return False, "Mohon maaf Ayah/Bunda, untuk detail pembayaran silakan hubungi admin secara langsung."

    if not context_available and needs_official_context(response_text):
        print("SafetyGuard: Response blocked because transactional answer has no official context.")
        return False, "Untuk data paket, harga, jadwal, hotel, maskapai, atau ketersediaan seat, saya bantu teruskan ke admin agar jawabannya sesuai data terbaru travel."

    return True, response_text
