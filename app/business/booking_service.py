import uuid
from typing import Tuple, Optional, Dict, Any
from sqlalchemy.orm import Session
from app.storage import models
from app.storage.repositories import BookingRepository, PackageRepository

def calculate_and_create_booking(
    db: Session,
    client_uuid: uuid.UUID,
    contact_id: uuid.UUID,
    conversation_id: uuid.UUID,
    package_code: str,
    customer_name: str,
    customer_phone: str,
    pax: int
) -> Tuple[Optional[models.Booking], bool, str]:
    """
    Calculates total cost and handles multi-pax limits:
    - >10 pax: triggers admin handover.
    - 6-10 pax: stores draft booking, delegates passenger collection mode to admin.
    - 2-5 pax: creates draft booking and configures placeholders.
    - 1 pax: creates draft booking.
    """
    # 1. Multi-pax validation check
    if pax > 10:
        # Hands over to admin immediately
        print(f"BookingService: Large group booking detected ({pax} pax). Handover required.")
        return None, True, "Mohon maaf Ayah/Bunda, untuk pemesanan rombongan besar (di atas 10 orang) akan saya sambungkan ke admin ya agar dibantu koordinasi jadwal & akomodasi secara khusus."

    # 2. Package price lookup
    pkg_repo = PackageRepository(db, client_uuid)
    package = pkg_repo.get_package_by_code(package_code)
    if not package:
        # Try finding any default/first package for development setup
        package = db.query(models.Package).filter(
            models.Package.client_id == client_uuid,
            models.Package.status == "active"
        ).first()
        
    if not package:
        print("BookingService: No active packages found.")
        return None, True, "Mohon maaf Ayah/Bunda, saat ini data paket sedang diperbarui oleh admin. Saya sambungkan ke admin dahulu ya."

    # Calculate price (strictly backend calculated)
    total_amount = package.price_per_pax * pax
    
    # 3. Handle passenger collection mode based on pax limits
    collection_mode = "bot_limited"
    if 6 <= pax <= 10:
        collection_mode = "admin_required"
        
    booking_repo = BookingRepository(db, client_uuid)
    booking = booking_repo.create_draft_booking(
        contact_id=contact_id,
        package_id=package.id,
        conversation_id=conversation_id,
        customer_name=customer_name,
        customer_phone=customer_phone,
        pax=pax,
        total_amount=total_amount
    )
    booking.passenger_collection_mode = collection_mode
    db.commit()

    # 4. Handle passenger records based on limit rules
    # Main passenger is order 1
    main_passenger = models.Passenger(
        client_id=client_uuid,
        booking_id=booking.id,
        passenger_order=1,
        full_name=customer_name,
        phone=customer_phone,
        status="complete" if pax == 1 else "partial"
    )
    db.add(main_passenger)

    if 2 <= pax <= 5:
        # Create passenger placeholders for the rest
        for i in range(2, pax + 1):
            placeholder = models.Passenger(
                client_id=client_uuid,
                booking_id=booking.id,
                passenger_order=i,
                status="placeholder"
            )
            db.add(placeholder)
            
    db.commit()
    db.refresh(booking)

    # 5. Build response message based on flow
    if 6 <= pax <= 10:
        response_msg = (
            f"Baik Ayah/Bunda, pemesanan untuk {pax} orang telah saya catat sebagai draft. "
            "Untuk rombongan 6-10 orang, data nama lengkap seluruh jamaah akan dibantu "
            "dan dikonfirmasi oleh admin secara langsung setelah ini ya."
        )
    elif 2 <= pax <= 5:
        response_msg = (
            f"Baik Ayah/Bunda, draft booking untuk {pax} orang sudah saya buat. "
            "Boleh dibantu sebutkan nama lengkap jamaah lainnya satu per satu?"
        )
    else:
        response_msg = f"Baik Ayah/Bunda, booking atas nama {customer_name} untuk 1 orang sudah saya catat."

    return booking, False, response_msg
