import datetime
import uuid
from typing import Tuple
from sqlalchemy.orm import Session
from app.storage import models
from app.storage.repositories import ContactRepository, LeadProfileRepository

def resolve_contact_and_lead(
    db: Session,
    client_id: uuid.UUID,
    phone_e164: str,
    sender_name: str,
    channel: str
) -> Tuple[models.Contact, models.LeadProfile]:
    """
    Finds or creates a Contact and maps/creates its Lead Profile.
    """
    contact_repo = ContactRepository(db, client_id)
    lead_repo = LeadProfileRepository(db, client_id)
    
    # Try finding existing contact
    contact = contact_repo.get_by_phone(phone_e164)
    if not contact:
        print(f"ContactResolver: Creating new contact for phone {phone_e164} under client {client_id}")
        contact = contact_repo.create(
            phone_e164=phone_e164,
            display_name=sender_name,
            source_channel=channel
        )
    else:
        # Update display name if updated
        if sender_name and contact.display_name != sender_name:
            contact.display_name = sender_name
            db.commit()

    # Find or create Lead Profile
    lead_profile = lead_repo.get_by_contact_id(contact.id)
    if not lead_profile:
        print(f"ContactResolver: Creating new lead profile for contact_id {contact.id}")
        lead_profile = lead_repo.create_or_update(
            contact_id=contact.id,
            stage="new_lead",
            last_engaged_at=datetime.datetime.now(datetime.timezone.utc)
        )
    else:
        # Update last engaged time
        lead_profile = lead_repo.create_or_update(
            contact_id=contact.id,
            last_engaged_at=datetime.datetime.now(datetime.timezone.utc)
        )
        
    return contact, lead_profile
