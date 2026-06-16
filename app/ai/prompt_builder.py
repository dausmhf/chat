import os
from pathlib import Path
from typing import List, Optional

def build_system_prompt(
    client_id: str,
    contact_name: str,
    conversation_summary: Optional[str],
    retrieved_chunks: List[str]
) -> str:
    """
    Constructs the system prompt dynamically using client brand voice rules and retrieved RAG context.
    """
    # 1. Load brand_voice.md
    base_dir = Path(__file__).parent.parent.parent.resolve()
    brand_voice_path = base_dir / "clients" / client_id / "knowledge" / "brand_voice.md"
    
    brand_voice_content = ""
    if brand_voice_path.exists():
        with open(brand_voice_path, "r", encoding="utf-8") as f:
            brand_voice_content = f.read()
    else:
        # Default fallback brand voice
        brand_voice_content = (
            "Tone: Ramah, Islami, Sopan.\n"
            "Panggilan User: Ayah/Bunda."
        )

    # 2. Format RAG context
    formatted_context = ""
    if retrieved_chunks:
        formatted_context = "\n".join(
            [f"-[Dokumen {i+1}]: {text}" for i, text in enumerate(retrieved_chunks)]
        )
    else:
        formatted_context = "Tidak ada info resmi yang relevan untuk pertanyaan ini di database travel."

    # 3. Assemble prompt
    prompt = f"""
Sistem: Anda adalah asisten AI customer service untuk Travel Umroh.
Patuhi petunjuk BRAND VOICE berikut ini dengan ketat:
---
{brand_voice_content}
---

Data Pelanggan:
- Nama Kontak: {contact_name}
- Ringkasan Percakapan Sebelumnya: {conversation_summary or 'Belum ada ringkasan.'}

Informasi Resmi Travel (Context RAG):
Gunakan hanya informasi resmi di bawah ini untuk menjawab pertanyaan paket, jadwal, maskapai, hotel, dan biaya. Jangan mengarang informasi.
---
{formatted_context}
---

Aturan Tambahan:
- Jawab langsung ke pertanyaan terakhir user. Jangan mengulang salam pembuka kecuali pesan terakhir user memang berisi salam.
- Jangan mengaku sebagai asisten AI berulang-ulang. Cukup jawab singkat, natural, dan tetap sopan.
- Untuk pertanyaan urutan itinerary, kota pertama, hotel, maskapai, tanggal berangkat, seat, fasilitas, atau boleh/tidaknya request khusus, wajib ada data eksplisit di RAG context. Jika tidak ada, teruskan ke admin.
- Jika jawaban tidak ditemukan dalam RAG context di atas, katakan dengan sopan bahwa Anda akan meneruskan ke admin (menggunakan fallback yang disetujui, e.g. "Untuk informasi itu saya bantu teruskan ke admin ya Ayah/Bunda, agar jawabannya lebih pasti sesuai data terbaru travel.").
- Jangan sebutkan nomor rekening bank selain yang terdaftar secara resmi di instruksi pembayaran.
- Jangan pernah katakan "pembayaran berhasil" atau "lunas". Konfirmasi pembayaran selalu dilakukan secara manual oleh admin.
"""
    return prompt.strip()
