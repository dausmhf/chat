import re
import uuid
from typing import Tuple, Optional
from sqlalchemy.orm import Session
from app.storage import models

KEYWORD_TRANSFER = r"\b(transfer|resi|tf|bukti|sudah\s*bayar|bayar|lunas|payment)\b"

def triage_incoming_file(
    db: Session,
    client_uuid: uuid.UUID,
    conversation: models.Conversation,
    message: models.Message
) -> Tuple[bool, str]:
    """
    Analyzes incoming files/images.
    If it represents payment evidence (waiting_payment_evidence status or caption keywords matched):
    - Inserts a record in payment_evidences
    - Locks conversation (status -> handover_required, bot_enabled -> False)
    - Triggers handover notification
    """
    if message.message_type not in {"image", "file"}:
        return False, ""

    caption = (message.text_content or "").lower().strip()
    is_evidence_by_status = conversation.status == "waiting_payment_evidence"
    is_evidence_by_caption = bool(re.search(KEYWORD_TRANSFER, caption))

    # We treat it as payment evidence if either condition is met
    if is_evidence_by_status or is_evidence_by_caption:
        print(f"PaymentService: Payment evidence detected from message {message.id}.")
        
        # 1. Resolve related invoice if available
        invoice = db.query(models.Invoice).join(
            models.Booking, models.Invoice.booking_id == models.Booking.id
        ).filter(
            models.Booking.contact_id == conversation.contact_id,
            models.Invoice.payment_status == "unpaid"
        ).order_by(models.Invoice.created_at.desc()).first()

        if not invoice:
            evidence = models.PaymentEvidence(
                client_id=client_uuid,
                invoice_id=None,
                conversation_id=conversation.id,
                message_id=message.id,
                file_path=message.file_path,
                file_url=message.file_url,
                caption=message.text_content,
                triage_status="needs_invoice_context",
                admin_validation_status="evidence_received"
            )
            db.add(evidence)
            db.commit()
            reply = (
                "Bukti sudah kami terima Ayah/Bunda, tapi saya belum menemukan invoice aktif yang cocok. "
                "Boleh kirim nomor invoice atau tunggu admin bantu cek konteksnya ya."
            )
            return False, reply

        # 2. Save payment evidence
        evidence = models.PaymentEvidence(
            client_id=client_uuid,
            invoice_id=invoice.id,
            conversation_id=conversation.id,
            message_id=message.id,
            file_path=message.file_path,
            file_url=message.file_url,
            caption=message.text_content,
            triage_status="likely_payment_evidence",
            admin_validation_status="evidence_received"
        )
        db.add(evidence)

        # 3. Lock Conversation (handover_required, bot_enabled -> False)
        conversation.status = "handover_required"
        conversation.bot_enabled = False

        # 4. Record handover event
        handover = models.HandoverEvent(
            client_id=client_uuid,
            conversation_id=conversation.id,
            contact_id=conversation.contact_id,
            reason="payment_evidence_received",
            status="open",
            summary=f"Bukti transfer diterima. File: {message.file_path or message.file_url}"
        )
        db.add(handover)
        
        db.commit()

        # Acknowledge receipt without validating (Policy rule)
        acknowledgement_reply = (
            "Baik Ayah/Bunda, bukti pembayaran sudah kami terima. "
            "Untuk keamanan transaksi, admin akan cek mutasi rekening resmi terlebih dahulu ya."
        )
        return True, acknowledgement_reply

    else:
        # File is received but doesn't look like payment evidence
        print("PaymentService: General file attachment received. No lock applied.")
        reply = "Terima kasih atas kiriman filenya Ayah/Bunda. Boleh saya tahu ini file mengenai apa ya?"
        return False, reply
