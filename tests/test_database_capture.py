import uuid
from sqlalchemy.orm import Session
from app.ingestion.event_handler import handle_incoming_webhook
from app.storage import models

def test_webhook_ingestion_and_idempotency(test_db: Session):
    client_id = uuid.UUID("11111111-1111-1111-1111-111111111111")
    payload = {
        "messageId": "unique_msg_1",
        "message": "Assalamu'alaikum",
        "phone": "628999999999",
        "name": "Ahmad",
        "client_id": "travel_alfalah"
    }

    # First webhook call should succeed
    res = handle_incoming_webhook(test_db, "starsender", payload)
    assert res["status"] == "success"
    
    # Assert database records are captured
    contact = test_db.query(models.Contact).filter(models.Contact.phone_e164 == "628999999999").first()
    assert contact is not None
    assert contact.display_name == "Ahmad"

    msg = test_db.query(models.Message).filter(models.Message.provider_message_id == "unique_msg_1").first()
    assert msg is not None
    assert msg.text_content == "Assalamu'alaikum"

    # Second identical webhook call should be blocked as duplicate
    res_dup = handle_incoming_webhook(test_db, "starsender", payload)
    assert res_dup["status"] == "duplicate"
    
    # Assert no duplicate message log is inserted
    msg_count = test_db.query(models.Message).filter(models.Message.contact_id == contact.id).count()
    assert msg_count == 1
