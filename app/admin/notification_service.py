import datetime
import uuid
from typing import Optional
from sqlalchemy.orm import Session
from app.storage import models


def create_admin_notification(
    db: Session,
    client_id: uuid.UUID,
    conversation: models.Conversation,
    reason: str,
    summary: str,
    contact: Optional[models.Contact] = None,
    lead_profile: Optional[models.LeadProfile] = None,
    invoice: Optional[models.Invoice] = None,
    priority: str = "high",
) -> models.QueueJob:
    """
    Records a durable admin notification job. A delivery worker can later route it
    to Telegram, WhatsApp admin group, email, or an internal dashboard.
    """
    payload = {
        "reason": reason,
        "conversation_id": str(conversation.id),
        "contact_name": contact.display_name if contact else None,
        "phone": contact.phone_e164 if contact else None,
        "lead_stage": lead_profile.stage if lead_profile else None,
        "invoice_number": invoice.invoice_number if invoice else None,
        "total_amount": invoice.amount if invoice else None,
        "summary": summary,
        "priority": priority,
    }
    job_key = f"admin-notify:{conversation.id}:{reason}:{datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d%H%M%S')}"
    job = models.QueueJob(
        client_id=client_id,
        queue_name="notification_queue",
        task_name="notify_admin",
        idempotency_key=job_key,
        payload=payload,
        status="pending",
        max_attempts=5,
    )
    db.add(job)
    db.add(models.AuditLog(
        client_id=client_id,
        actor_type="system",
        event_type="admin_notification_queued",
        entity_type="conversations",
        entity_id=conversation.id,
        new_value=payload,
    ))
    db.commit()
    db.refresh(job)
    return job


def create_handover(
    db: Session,
    client_id: uuid.UUID,
    conversation: models.Conversation,
    reason: str,
    summary: str,
    contact: Optional[models.Contact] = None,
    lead_profile: Optional[models.LeadProfile] = None,
    invoice: Optional[models.Invoice] = None,
) -> models.HandoverEvent:
    conversation.status = "handover_required"
    conversation.bot_enabled = False
    handover = models.HandoverEvent(
        client_id=client_id,
        conversation_id=conversation.id,
        contact_id=conversation.contact_id,
        reason=reason,
        status="open",
        summary=summary,
    )
    db.add(handover)
    db.commit()
    db.refresh(handover)
    create_admin_notification(db, client_id, conversation, reason, summary, contact, lead_profile, invoice)
    return handover
