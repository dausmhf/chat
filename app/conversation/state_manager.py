import datetime
from sqlalchemy.orm import Session
from app.storage import models

# Forbidden bot auto-reply statuses
BLOCKED_BOT_STATUSES = {"handover_required", "human_active", "abuse_limited"}

def can_bot_reply(conversation: models.Conversation) -> bool:
    """
    Returns True if the bot is enabled and conversation status allows auto-reply.
    """
    if not conversation.bot_enabled:
        return False
    if conversation.status in BLOCKED_BOT_STATUSES:
        return False
    return True

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

def uuid_from_str(val: str) -> datetime.UUID:
    try:
        import uuid
        return uuid.UUID(val)
    except ValueError:
        return None
