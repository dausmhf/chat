import uuid
from sqlalchemy.orm import Session
from app.business.invoice_service import create_booking_invoice
from app.storage import models

def test_invoice_creation_and_revisions(test_db: Session):
    client_uuid = uuid.UUID("11111111-1111-1111-1111-111111111111")
    
    # Setup booking
    booking = models.Booking(
        client_id=client_uuid,
        contact_id=uuid.uuid4(),
        package_id=uuid.UUID("22222222-2222-2222-2222-222222222222"),
        customer_name="Muhammad Firdaus",
        customer_phone="628123456789",
        pax=2,
        total_amount=58000000,
        status="draft"
    )
    test_db.add(booking)
    test_db.commit()

    # Generate initial invoice
    inv_1, pdf_1 = create_booking_invoice(test_db, client_uuid, "travel_alfalah", booking.id)
    assert inv_1 is not None
    assert inv_1.revision_number == 1
    assert inv_1.payment_status == "unpaid"
    
    # Verify booking status transitioned
    assert booking.status == "invoice_sent"

    # Generate revision invoice
    inv_2, pdf_2 = create_booking_invoice(test_db, client_uuid, "travel_alfalah", booking.id)
    assert inv_2 is not None
    assert inv_2.invoice_number == inv_1.invoice_number # Immutable invoice number
    assert inv_2.revision_number == 2
    assert pdf_2 != pdf_1 # File path changed (distinct revision filename)
