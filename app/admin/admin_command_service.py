import datetime
import uuid
from typing import Tuple, Dict, Any
from sqlalchemy.orm import Session
from app.storage import models
from app.storage.repositories import ConversationRepository

def execute_bot_on(
    db: Session,
    client_uuid: uuid.UUID,
    conversation_id: uuid.UUID,
    reason: str,
    admin_id: Optional[uuid.UUID] = None
) -> Tuple[bool, str]:
    """
    Admin command: /bot_on
    Returns control of the conversation to the bot.
    """
    conv_repo = ConversationRepository(db, client_uuid)
    conversation = conv_repo.get_by_id(conversation_id)
    if not conversation:
        return False, f"Conversation {conversation_id} not found."

    # 1. Update status and bot_enabled flag
    conversation.status = "bot_active"
    conversation.bot_enabled = True
    conversation.updated_at = datetime.datetime.now(datetime.timezone.utc)

    # 2. Save audit log
    audit = models.AuditLog(
        client_id=client_uuid,
        actor_type="admin",
        actor_id=admin_id,
        event_type="bot_turned_on",
        entity_type="conversations",
        entity_id=conversation_id,
        old_value={"status": conversation.status, "bot_enabled": False},
        new_value={"status": "bot_active", "bot_enabled": True},
        meta_data={"reason": reason}
    )
    db.add(audit)
    db.commit()

    # 3. Message template response to WhatsApp
    confirm_msg = (
        "Baik Ayah/Bunda, admin sudah membantu pengecekan. "
        "Jika ada pertanyaan lain tentang paket, saya siap bantu kembali ya."
    )
    return True, confirm_msg

def execute_takeover(
    db: Session,
    client_uuid: uuid.UUID,
    conversation_id: uuid.UUID,
    admin_id: Optional[uuid.UUID] = None
) -> Tuple[bool, str]:
    """
    Admin command: /takeover
    Turns bot reply off and marks status as human_active.
    """
    conv_repo = ConversationRepository(db, client_uuid)
    conversation = conv_repo.get_by_id(conversation_id)
    if not conversation:
        return False, f"Conversation {conversation_id} not found."

    conversation.status = "human_active"
    conversation.bot_enabled = False
    conversation.updated_at = datetime.datetime.now(datetime.timezone.utc)

    audit = models.AuditLog(
        client_id=client_uuid,
        actor_type="admin",
        actor_id=admin_id,
        event_type="takeover",
        entity_type="conversations",
        entity_id=conversation_id,
        old_value={"status": conversation.status, "bot_enabled": True},
        new_value={"status": "human_active", "bot_enabled": False}
    )
    db.add(audit)
    db.commit()

    return True, "Conversation is taken over by admin. Bot replies are paused."

def execute_mark_payment(
    db: Session,
    client_uuid: uuid.UUID,
    invoice_id: uuid.UUID,
    new_status: str,  # paid, invalid, cancelled
    admin_id: Optional[uuid.UUID] = None,
    note: Optional[str] = None
) -> Tuple[bool, str]:
    """
    Admin command: /mark_payment
    Only admin can trigger this status change. Auto-payment triggers are forbidden.
    """
    if new_status not in {"paid", "invalid", "cancelled"}:
        return False, "Invalid status. Allowed values: paid, invalid, cancelled."

    invoice = db.query(models.Invoice).filter(
        models.Invoice.id == invoice_id,
        models.Invoice.client_id == client_uuid
    ).first()
    
    if not invoice:
        return False, f"Invoice {invoice_id} not found."

    old_status = invoice.payment_status
    invoice.payment_status = new_status
    invoice.updated_at = datetime.datetime.now(datetime.timezone.utc)

    # Transition booking status accordingly
    booking = db.query(models.Booking).filter(models.Booking.id == invoice.booking_id).first()
    if booking:
        old_booking_status = booking.status
        if new_status == "paid":
            booking.status = "confirmed"
        elif new_status == "cancelled":
            booking.status = "cancelled"
        elif new_status == "invalid":
            booking.status = "waiting_payment"
            
        booking.updated_at = datetime.datetime.now(datetime.timezone.utc)

    # Update related payment evidence validation statuses if they exist
    evidences = db.query(models.PaymentEvidence).filter(
        models.PaymentEvidence.invoice_id == invoice_id
    ).all()
    for ev in evidences:
        ev.admin_validation_status = new_status
        ev.validated_by_admin_id = admin_id
        ev.validated_at = datetime.datetime.now(datetime.timezone.utc)
        ev.admin_note = note

    audit = models.AuditLog(
        client_id=client_uuid,
        actor_type="admin",
        actor_id=admin_id,
        event_type="payment_marked",
        entity_type="invoices",
        entity_id=invoice_id,
        old_value={"payment_status": old_status},
        new_value={"payment_status": new_status},
        meta_data={"note": note}
    )
    db.add(audit)
    db.commit()

    return True, f"Invoice {invoice.invoice_number} successfully marked as '{new_status}'."

# Type imports
from typing import Optional
