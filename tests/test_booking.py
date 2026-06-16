import uuid
from sqlalchemy.orm import Session
from app.business.booking_service import calculate_and_create_booking
from app.storage import models

def test_booking_pax_rules(test_db: Session):
    client_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    contact_id = uuid.uuid4()
    conv_id = uuid.uuid4()

    # Case 1: 1 Pax Booking (Direct creation, status check)
    booking, requires_handover, msg = calculate_and_create_booking(
        test_db, client_uuid, contact_id, conv_id, "OCT12D", "Daus", "628123456", 1
    )
    assert booking is not None
    assert requires_handover is False
    assert booking.pax == 1
    assert booking.passenger_collection_mode == "bot_limited"
    assert booking.total_amount == 29000000 # 29M * 1
    
    # Case 2: 3 Pax Booking (Placeholders generated)
    booking_3, req_3, msg_3 = calculate_and_create_booking(
        test_db, client_uuid, contact_id, conv_id, "OCT12D", "Daus", "628123456", 3
    )
    assert booking_3 is not None
    assert req_3 is False
    assert booking_3.pax == 3
    assert booking_3.total_amount == 87000000 # 29M * 3
    
    # Assert passenger rows exist (1 main + 2 placeholders = 3 rows)
    passengers = test_db.query(models.Passenger).filter(models.Passenger.booking_id == booking_3.id).all()
    assert len(passengers) == 3
    assert passengers[0].passenger_order == 1
    assert passengers[1].passenger_order == 2
    assert passengers[1].status == "placeholder"

    # Case 3: 7 Pax Booking (Delegate details collection to admin)
    booking_7, req_7, msg_7 = calculate_and_create_booking(
        test_db, client_uuid, contact_id, conv_id, "OCT12D", "Daus", "628123456", 7
    )
    assert booking_7 is not None
    assert req_7 is False
    assert booking_7.passenger_collection_mode == "admin_required"

    # Case 4: 15 Pax Booking (Immediate admin handover)
    booking_large, req_large, msg_large = calculate_and_create_booking(
        test_db, client_uuid, contact_id, conv_id, "OCT12D", "Daus", "628123456", 15
    )
    assert booking_large is None
    assert req_large is True
