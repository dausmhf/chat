import datetime
import uuid
from dataclasses import dataclass
from sqlalchemy.orm import Session
from app.storage import models


@dataclass
class RateLimitDecision:
    allowed: bool
    state: str
    reason: str
    user_message: str = ""


def check_rate_limit(
    db: Session,
    client_id: uuid.UUID,
    contact_id: uuid.UUID,
    conversation: models.Conversation,
    latest_text: str = "",
    burst_limit: int = 10,
    daily_limit: int = 300,
    repeated_limit: int = 5,
) -> RateLimitDecision:
    """
    Applies PRD anti-abuse defaults using captured messages as the source of truth.
    """
    is_sqlite = db.bind.dialect.name == "sqlite" if db.bind else True
    if is_sqlite:
        now_naive = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
        one_minute_ago = (now_naive - datetime.timedelta(seconds=60)).strftime("%Y-%m-%d %H:%M:%S")
        one_day_ago = (now_naive - datetime.timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
    else:
        now = datetime.datetime.now(datetime.timezone.utc)
        one_minute_ago = now - datetime.timedelta(seconds=60)
        one_day_ago = now - datetime.timedelta(days=1)

    burst_count = db.query(models.Message).filter(
        models.Message.client_id == client_id,
        models.Message.contact_id == contact_id,
        models.Message.direction == "incoming",
        models.Message.created_at >= one_minute_ago,
    ).count()

    if burst_count >= burst_limit:
        conversation.status = "abuse_limited"
        conversation.bot_enabled = False
        _audit_limit(db, client_id, conversation.id, "burst_limit", {"count": burst_count})
        db.commit()
        return RateLimitDecision(
            allowed=False,
            state="cooldown",
            reason="burst_limit",
            user_message="Mohon tunggu sebentar ya Ayah/Bunda. Pesan yang masuk terlalu cepat, saya jeda dulu beberapa menit agar tidak terjadi dobel proses.",
        )

    daily_count = db.query(models.Message).filter(
        models.Message.client_id == client_id,
        models.Message.contact_id == contact_id,
        models.Message.direction == "incoming",
        models.Message.created_at >= one_day_ago,
    ).count()

    if daily_count >= daily_limit:
        conversation.status = "abuse_limited"
        conversation.bot_enabled = False
        _audit_limit(db, client_id, conversation.id, "daily_limit", {"count": daily_count})
        db.commit()
        return RateLimitDecision(
            allowed=False,
            state="abuse_limited",
            reason="daily_limit",
            user_message="Mohon maaf Ayah/Bunda, percakapan ini sedang kami batasi sementara dan akan dicek admin.",
        )

    if latest_text:
        recent = db.query(models.Message).filter(
            models.Message.client_id == client_id,
            models.Message.contact_id == contact_id,
            models.Message.direction == "incoming",
            models.Message.text_content.isnot(None),
        ).order_by(models.Message.created_at.desc()).limit(repeated_limit).all()
        normalized = latest_text.strip().lower()
        repeated = len(recent) >= repeated_limit and all(
            (msg.text_content or "").strip().lower() == normalized for msg in recent
        )
        if repeated:
            conversation.status = "abuse_limited"
            conversation.bot_enabled = False
            _audit_limit(db, client_id, conversation.id, "repeated_identical_message", {"text": normalized[:120]})
            db.commit()
            return RateLimitDecision(
                allowed=False,
                state="spam_suspected",
                reason="repeated_identical_message",
                user_message="Pesan yang sama terkirim berkali-kali, jadi saya hentikan balasan otomatis dulu dan teruskan ke admin ya Ayah/Bunda.",
            )

    return RateLimitDecision(allowed=True, state="normal", reason="ok")


def _audit_limit(db: Session, client_id: uuid.UUID, conversation_id: uuid.UUID, reason: str, metadata: dict) -> None:
    db.add(models.AuditLog(
        client_id=client_id,
        actor_type="system",
        event_type="rate_limit_applied",
        entity_type="conversations",
        entity_id=conversation_id,
        meta_data={"reason": reason, **metadata},
    ))
