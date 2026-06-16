import uuid
from sqlalchemy.orm import Session
from app.admin.admin_command_service import execute_bot_on, execute_takeover
from app.storage import models

def test_admin_handover_override_controls(test_db: Session):
    client_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    
    # Setup conversation
    conv = models.Conversation(
        client_id=client_uuid,
        contact_id=uuid.uuid4(),
        channel="starsender",
        status="handover_required",
        bot_enabled=False
    )
    test_db.add(conv)
    test_db.commit()

    # Trigger admin bot recovery: /bot_on
    success, reply = execute_bot_on(test_db, client_uuid, conv.id, "Admin verified payment")
    assert success is True
    assert conv.status == "bot_active"
    assert conv.bot_enabled is True
    assert "admin sudah membantu pengecekan" in reply

    # Trigger admin takeover: /takeover
    success_takeover, msg = execute_takeover(test_db, client_uuid, conv.id)
    assert success_takeover is True
    assert conv.status == "human_active"
    assert conv.bot_enabled is False
