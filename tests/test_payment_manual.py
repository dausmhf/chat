import uuid
from sqlalchemy.orm import Session
from app.payment.manual_payment_service import triage_incoming_file
from app.admin.admin_command_service import execute_mark_payment
from app.storage import models

def test_payment_evidence_triage_locks(test_db: Session):
    client_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    
    # Setup conversation and invoice
    conv = models.Conversation(
        client_id=client_uuid,
        contact_id=uuid.uuid4(),
        channel="starsender",
        status="waiting_payment_evidence",
        bot_enabled=True
    )
    test_db.add(conv)
    
    booking = models.Booking(
        client_id=client_uuid,
        contact_id=conv.contact_id,
        package_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        customer_name="Muhammad Firdaus",
        customer_phone="628123456789",
        pax=1,
        total_amount=29000000
    )
    test_db.add(booking)
    test_db.commit()

    invoice = models.Invoice(
        client_id=client_uuid,
        booking_id=booking.id,
        invoice_number="INV-TEST-001",
        revision_number=1,
        amount=29000000,
        pdf_path="dummy.pdf",
        payment_status="unpaid",
        due_at=datetime_future(),
        bank_account_version=1,
        package_price_version=1
    )
    test_db.add(invoice)
    test_db.commit()

    # Image message simulating bukti bayar
    msg = models.Message(
        client_id=client_uuid,
        conversation_id=conv.id,
        direction="incoming",
        message_type="image",
        file_path="bukti_transfer.jpg",
        text_content="Ini bukti bayarnya"
    )
    test_db.add(msg)
    test_db.commit()

    # Ingest file
    is_evidence, reply = triage_incoming_file(test_db, client_uuid, conv, msg)
    assert is_evidence is True
    assert "bukti pembayaran sudah kami terima" in reply
    
    # Assert conversation status locked
    assert conv.status == "handover_required"
    assert conv.bot_enabled is False

    # Try marking payment status (Forbidden for auto actions, must be triggered via admin service)
    success, audit_msg = execute_mark_payment(test_db, client_uuid, invoice.id, "paid")
    assert success is True
    assert invoice.payment_status == "paid"
    assert booking.status == "confirmed"

def datetime_future():
    import datetime
    return datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
