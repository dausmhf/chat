import datetime
import uuid
from typing import List, Optional
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.ai.llm_client import LLMClient
from app.storage import models


SUMMARY_TRIGGER_MESSAGE_COUNT = 10  # generate summary every N messages
SUMMARY_TRIGGER_AGE_MINUTES = 60    # or if last summary older than this
SUMMARY_MAX_MESSAGES = 40           # max recent messages to include in summary context
SUMMARY_MAX_TOKENS = 500            # max output tokens for summary


def maybe_summarize_conversation(
    db: Session,
    client_uuid: uuid.UUID,
    client_code: str,
    conversation: models.Conversation,
    contact: models.Contact,
    llm_client: LLMClient,
    configs: dict,
) -> Optional[str]:
    """
    Checks if a conversation needs summarization and runs it if so.
    Returns the new summary text, or None if no update needed.
    """
    # Check trigger conditions
    message_count = (
        db.query(func.count(models.Message.id))
        .filter(
            models.Message.client_id == client_uuid,
            models.Message.conversation_id == conversation.id,
            models.Message.text_content.isnot(None),
        )
        .scalar()
    )

    now = datetime.datetime.now(datetime.timezone.utc)
    needs_update = False

    if not conversation.summary:
        needs_update = message_count >= SUMMARY_TRIGGER_MESSAGE_COUNT
    elif conversation.summary_updated_at:
        summary_updated_at = conversation.summary_updated_at
        if summary_updated_at.tzinfo is None:
            now_compare = datetime.datetime.utcnow()
        else:
            now_compare = now
        age_minutes = (now_compare - summary_updated_at).total_seconds() / 60
        needs_update = age_minutes >= SUMMARY_TRIGGER_AGE_MINUTES and message_count >= 4
    else:
        needs_update = message_count >= SUMMARY_TRIGGER_MESSAGE_COUNT

    if not needs_update:
        return None

    # Fetch recent messages for context
    recent = (
        db.query(models.Message)
        .filter(
            models.Message.client_id == client_uuid,
            models.Message.conversation_id == conversation.id,
            models.Message.text_content.isnot(None),
        )
        .order_by(models.Message.created_at.desc())
        .limit(SUMMARY_MAX_MESSAGES)
        .all()
    )

    if len(recent) < 4:
        return None

    # Build message transcript
    transcript_lines: List[str] = []
    for msg in reversed(recent):
        role = "Ayah/Bunda" if msg.direction == "incoming" else "Bot"
        text = (msg.text_content or "").strip()
        if not text:
            continue
        transcript_lines.append(f"{role}: {text}")

    if not transcript_lines:
        return None

    transcript = "\n".join(transcript_lines)

    # Build system prompt for summarization
    contact_name = contact.display_name or "Ayah/Bunda"
    system_prompt = f"""Anda adalah sistem peringkas percakapan AI customer service Travel Umroh.
Ringkas percakapan di bawah ini dalam 2-4 kalimat Bahasa Indonesia. Sertakan:

1. Identitas customer (nama: {contact_name})
2. Topik utama yang dibahas (paket, harga, booking, pembayaran, dll)
3. Status terakhir (apakah sudah booking? sudah ada invoice? masih tanya-tanya?)
4. Jika ada keputusan atau tindakan penting, sebutkan

Gunakan tone netral dan faktual. Jangan tambahkan informasi yang tidak ada di transkrip."""

    try:
        summary = llm_client.generate_chat_response(
            messages=[{"role": "user", "content": f"Ringkas percakapan berikut:\n\n{transcript}"}],
            system_prompt=system_prompt,
            temperature=0.15,
            max_tokens=SUMMARY_MAX_TOKENS,
        )
        summary = summary.strip()
    except Exception as exc:
        print(f"Summarizer: LLM call failed: {exc}")
        return None

    if len(summary) < 20:
        return None

    # Save to database
    conversation.summary = summary
    conversation.summary_updated_at = now
    db.add(models.AuditLog(
        client_id=client_uuid,
        actor_type="system",
        event_type="conversation_summarized",
        entity_type="conversations",
        entity_id=conversation.id,
        new_value={"summary": summary, "message_count": message_count},
    ))
    db.commit()

    print(f"Summarizer: Generated summary for conversation {conversation.id} ({message_count} messages)")
    return summary
