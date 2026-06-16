import uuid
from sqlalchemy.orm import Session

from app.ai.safety_guard import validate_response
from app.conversation.orchestrator import process_incoming_message
from app.payment.manual_payment_service import triage_incoming_file
from app.storage import models


def test_orchestrator_ask_price_replies_and_logs_intent(test_db: Session):
    payload = {
        "messageId": "orch_price_1",
        "message": "Berapa harga paket Oktober?",
        "phone": "628111111111",
        "name": "Ahmad",
        "client_id": "travel_alfalah",
    }

    result = process_incoming_message(test_db, "starsender", payload, send_reply=False)

    assert result["status"] == "replied"
    assert result["intent"] == "ask_price"
    outgoing = test_db.query(models.Message).filter(models.Message.direction == "outgoing").first()
    assert outgoing is not None
    intent = test_db.query(models.IntentResult).filter(models.IntentResult.intent == "ask_price").first()
    assert intent is not None


def test_orchestrator_booking_creates_invoice_and_outgoing_reply(test_db: Session):
    payload = {
        "messageId": "orch_booking_1",
        "message": "Saya mau booking 2 pax",
        "phone": "628222222222",
        "name": "Fatimah",
        "client_id": "travel_alfalah",
    }

    result = process_incoming_message(test_db, "starsender", payload, send_reply=False)

    assert result["status"] == "replied"
    assert result["intent"] == "booking_intent"
    assert result["booking_id"]
    assert result["invoice_id"]
    invoice = test_db.query(models.Invoice).filter(models.Invoice.id == uuid.UUID(result["invoice_id"])).first()
    assert invoice is not None
    assert invoice.payment_status == "unpaid"


def test_itinerary_order_question_without_rag_goes_to_admin(test_db: Session):
    payload = {
        "messageId": "orch_itinerary_1",
        "message": "Kalo Mekkah dulu boleh nggak?",
        "phone": "628333333333",
        "name": "Rani",
        "client_id": "travel_alfalah",
    }

    result = process_incoming_message(test_db, "starsender", payload, send_reply=False)

    assert result["status"] == "handover"
    assert "admin" in result["reply"].lower()
    assert "madinah terlebih dahulu" not in result["reply"].lower()


def test_payment_evidence_without_invoice_does_not_lock(test_db: Session):
    client_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    conv = models.Conversation(
        client_id=client_uuid,
        contact_id=uuid.uuid4(),
        channel="starsender",
        status="waiting_payment_evidence",
        bot_enabled=True,
    )
    test_db.add(conv)
    test_db.commit()

    msg = models.Message(
        client_id=client_uuid,
        conversation_id=conv.id,
        contact_id=conv.contact_id,
        direction="incoming",
        message_type="image",
        file_path="bukti.jpg",
        text_content="bukti transfer",
    )
    test_db.add(msg)
    test_db.commit()

    is_evidence, reply = triage_incoming_file(test_db, client_uuid, conv, msg)

    assert is_evidence is False
    assert "belum menemukan invoice aktif" in reply
    assert conv.status == "waiting_payment_evidence"
    assert conv.bot_enabled is True


def test_safety_blocks_transactional_answer_without_context():
    is_safe, fallback = validate_response(
        "Harga paket ini 29 juta dan hotelnya pasti bintang lima.",
        {"bank_accounts": []},
        context_available=False,
        intent="ask_price",
    )

    assert is_safe is False
    assert "saya bantu teruskan ke admin" in fallback
