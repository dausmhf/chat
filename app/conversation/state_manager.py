import datetime
import uuid
from typing import Tuple
from sqlalchemy.orm import Session
from app.storage import models

# Forbidden bot auto-reply statuses
BLOCKED_BOT_STATUSES = {"handover_required", "human_active", "abuse_limited"}

# Auto-recovery timeouts (seconds)
RATE_LIMIT_COOLDOWN_SECONDS = 300   # 5 minutes
HANDOVER_AUTO_RECOVER_SECONDS = 86400  # 24 hours


def can_bot_reply(conversation: models.Conversation) -> bool:
    """
    Returns True if the bot is enabled and conversation status allows auto-reply.
    Also checks for auto-recovery timeouts.
    """
    if not conversation.bot_enabled:
        # Check auto-recovery for abuse_limited (rate limit cooldown)
        if conversation.status == "abuse_limited" and conversation.updated_at:
            elapsed = (datetime.datetime.now(datetime.timezone.utc) - conversation.updated_at).total_seconds()
            if elapsed >= RATE_LIMIT_COOLDOWN_SECONDS:
                return True  # will trigger auto-recovery in orchestrator
        return False

    if conversation.status in BLOCKED_BOT_STATUSES:
        # Check auto-recovery for handover timeout
        if conversation.status == "handover_required" and conversation.updated_at:
            elapsed = (datetime.datetime.now(datetime.timezone.utc) - conversation.updated_at).total_seconds()
            if elapsed >= HANDOVER_AUTO_RECOVER_SECONDS:
                return True  # will trigger auto-recovery in orchestrator
        return False

    return True


def check_and_auto_recover(
    db: Session,
    conversation: models.Conversation,
) -> Tuple[bool, str]:
    """
    Checks if conversation should auto-recover from rate-limit or handover.
    Returns (recovered, reason).
    """
    now = datetime.datetime.now(datetime.timezone.utc)

    if conversation.status == "abuse_limited" and conversation.updated_at:
        elapsed = (now - conversation.updated_at).total_seconds()
        if elapsed >= RATE_LIMIT_COOLDOWN_SECONDS:
            conversation.status = "bot_active"
            conversation.bot_enabled = True
            conversation.updated_at = now
            db.add(models.AuditLog(
                client_id=conversation.client_id,
                actor_type="system",
                event_type="rate_limit_auto_recovered",
                entity_type="conversations",
                entity_id=conversation.id,
                meta_data={"cooldown_seconds": RATE_LIMIT_COOLDOWN_SECONDS, "elapsed_seconds": elapsed},
            ))
            db.commit()
            return True, "rate_limit_cooldown"

    if conversation.status == "handover_required" and conversation.updated_at:
        elapsed = (now - conversation.updated_at).total_seconds()
        if elapsed >= HANDOVER_AUTO_RECOVER_SECONDS:
            conversation.status = "bot_active"
            conversation.bot_enabled = True
            conversation.updated_at = now
            db.add(models.AuditLog(
                client_id=conversation.client_id,
                actor_type="system",
                event_type="handover_auto_recovered",
                entity_type="conversations",
                entity_id=conversation.id,
                meta_data={"timeout_seconds": HANDOVER_AUTO_RECOVER_SECONDS, "elapsed_seconds": elapsed},
            ))
            db.commit()
            return True, "handover_timeout"

    return False, ""

def transition_status(
    db: Session,
    conversation: models.Conversation,
    new_status: str,
    actor_type: str = "system",
    actor_id: str = None
) -> models.Conversation:
    """
    Updates the conversation status and captures it in audit logs.
    """
    old_status = conversation.status
    if old_status == new_status:
        return conversation

    conversation.status = new_status
    conversation.updated_at = datetime.datetime.now(datetime.timezone.utc)
    
    # Log audit event
    audit = models.AuditLog(
        client_id=conversation.client_id,
        actor_type=actor_type,
        actor_id=uuid_from_str(actor_id) if actor_id else None,
        event_type="conversation_status_changed",
        entity_type="conversations",
        entity_id=conversation.id,
        old_value={"status": old_status},
        new_value={"status": new_status}
    )
    db.add(audit)
    db.commit()
    db.refresh(conversation)
    return conversation

def uuid_from_str(val: str) -> uuid.UUID:
    try:
        return uuid.UUID(val)
    except ValueError:
        return None
