import datetime
import uuid
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from app.storage import models

class BaseRepository:
    def __init__(self, db: Session, client_id: uuid.UUID):
        self.db = db
        self.client_id = client_id

class ContactRepository(BaseRepository):
    def get_by_phone(self, phone_e164: str) -> Optional[models.Contact]:
        return self.db.query(models.Contact).filter(
            models.Contact.client_id == self.client_id,
            models.Contact.phone_e164 == phone_e164
        ).first()

    def create(self, phone_e164: str, display_name: Optional[str] = None, city: Optional[str] = None, source_channel: Optional[str] = None) -> models.Contact:
        contact = models.Contact(
            client_id=self.client_id,
            phone_e164=phone_e164,
            display_name=display_name,
            city=city,
            source_channel=source_channel,
            consent_status="unknown"
        )
        self.db.add(contact)
        self.db.commit()
        self.db.refresh(contact)
        return contact

class LeadProfileRepository(BaseRepository):
    def get_by_contact_id(self, contact_id: uuid.UUID) -> Optional[models.LeadProfile]:
        return self.db.query(models.LeadProfile).filter(
            models.LeadProfile.client_id == self.client_id,
            models.LeadProfile.contact_id == contact_id
        ).first()

    def create_or_update(self, contact_id: uuid.UUID, **kwargs) -> models.LeadProfile:
        profile = self.get_by_contact_id(contact_id)
        if not profile:
            profile = models.LeadProfile(
                client_id=self.client_id,
                contact_id=contact_id,
                stage="new_lead"
            )
            self.db.add(profile)
        
        for key, value in kwargs.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        
        profile.updated_at = datetime.datetime.now(datetime.timezone.utc)
        self.db.commit()
        self.db.refresh(profile)
        return profile

class ConversationRepository(BaseRepository):
    def get_by_contact_id(self, contact_id: uuid.UUID) -> Optional[models.Conversation]:
        return self.db.query(models.Conversation).filter(
            models.Conversation.client_id == self.client_id,
            models.Conversation.contact_id == contact_id
        ).first()

    def get_by_id(self, conversation_id: uuid.UUID) -> Optional[models.Conversation]:
        return self.db.query(models.Conversation).filter(
            models.Conversation.client_id == self.client_id,
            models.Conversation.id == conversation_id
        ).first()

    def create_or_update(self, contact_id: uuid.UUID, channel: str, **kwargs) -> models.Conversation:
        conv = self.get_by_contact_id(contact_id)
        if not conv:
            conv = models.Conversation(
                client_id=self.client_id,
                contact_id=contact_id,
                channel=channel,
                status="bot_active",
                bot_enabled=True
            )
            self.db.add(conv)
            
        for key, value in kwargs.items():
            if hasattr(conv, key):
                setattr(conv, key, value)
        
        conv.updated_at = datetime.datetime.now(datetime.timezone.utc)
        self.db.commit()
        self.db.refresh(conv)
        return conv

class MessageRepository(BaseRepository):
    def log_message(self, conversation_id: uuid.UUID, direction: str, message_type: str, **kwargs) -> models.Message:
        msg = models.Message(
            client_id=self.client_id,
            conversation_id=conversation_id,
            direction=direction,
            message_type=message_type,
            sender_type=kwargs.get("sender_type", "user" if direction == "incoming" else "bot"),
            text_content=kwargs.get("text_content"),
            file_url=kwargs.get("file_url"),
            file_path=kwargs.get("file_path"),
            content_hash=kwargs.get("content_hash"),
            provider_message_id=kwargs.get("provider_message_id"),
            meta_data=kwargs.get("metadata", {}),
            contact_id=kwargs.get("contact_id"),
            raw_payload_id=kwargs.get("raw_payload_id")
        )
        self.db.add(msg)
        
        # Update last_message_at on conversation
        conv = self.db.query(models.Conversation).filter(models.Conversation.id == conversation_id).first()
        if conv:
            conv.last_message_at = datetime.datetime.now(datetime.timezone.utc)
            
        self.db.commit()
        self.db.refresh(msg)
        return msg

class IdempotencyRepository(BaseRepository):
    def check_and_insert(self, idempotency_key: str, channel: str, event_type: str, expires_at: datetime.datetime, raw_payload_hash: Optional[str] = None) -> bool:
        """
        Attempts to insert idempotency key. Returns True if inserted successfully (first seen),
        or False if duplicate was found.
        """
        # Query first to see if duplicate exists
        existing = self.db.query(models.WebhookIdempotencyKey).filter(
            models.WebhookIdempotencyKey.client_id == self.client_id,
            models.WebhookIdempotencyKey.channel == channel,
            models.WebhookIdempotencyKey.idempotency_key == idempotency_key
        ).first()

        if existing:
            existing.duplicate_count += 1
            existing.last_seen_at = datetime.datetime.now(datetime.timezone.utc)
            self.db.commit()
            return False

        key_record = models.WebhookIdempotencyKey(
            client_id=self.client_id,
            channel=channel,
            idempotency_key=idempotency_key,
            event_type=event_type,
            expires_at=expires_at,
            raw_payload_hash=raw_payload_hash,
            duplicate_count=0
        )
        try:
            self.db.add(key_record)
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            # If database concurrent constraint failed, try to update existing
            existing = self.db.query(models.WebhookIdempotencyKey).filter(
                models.WebhookIdempotencyKey.client_id == self.client_id,
                models.WebhookIdempotencyKey.channel == channel,
                models.WebhookIdempotencyKey.idempotency_key == idempotency_key
            ).first()
            if existing:
                existing.duplicate_count += 1
                existing.last_seen_at = datetime.datetime.now(datetime.timezone.utc)
                self.db.commit()
            return False

class PackageRepository(BaseRepository):
    def get_packages(self, status: str = "active") -> List[models.Package]:
        return self.db.query(models.Package).filter(
            models.Package.client_id == self.client_id,
            models.Package.status == status
        ).all()

    def get_package_by_code(self, package_code: str) -> Optional[models.Package]:
        return self.db.query(models.Package).filter(
            models.Package.client_id == self.client_id,
            models.Package.package_code == package_code,
            models.Package.status == "active"
        ).order_by(models.Package.price_version.desc()).first()

class BookingRepository(BaseRepository):
    def create_draft_booking(self, contact_id: uuid.UUID, package_id: uuid.UUID, customer_name: str, customer_phone: str, pax: int, total_amount: int, conversation_id: Optional[uuid.UUID] = None) -> models.Booking:
        booking = models.Booking(
            client_id=self.client_id,
            contact_id=contact_id,
            package_id=package_id,
            conversation_id=conversation_id,
            customer_name=customer_name,
            customer_phone=customer_phone,
            pax=pax,
            total_amount=total_amount,
            status="draft"
        )
        self.db.add(booking)
        self.db.commit()
        self.db.refresh(booking)
        return booking

class InvoiceRepository(BaseRepository):
    def create_invoice(self, booking_id: uuid.UUID, invoice_number: str, amount: int, pdf_path: str, due_at: datetime.datetime, bank_account_id: uuid.UUID, bank_account_version: int, package_price_version: int) -> models.Invoice:
        invoice = models.Invoice(
            client_id=self.client_id,
            booking_id=booking_id,
            invoice_number=invoice_number,
            revision_number=1,
            amount=amount,
            currency="IDR",
            pdf_path=pdf_path,
            payment_status="unpaid",
            due_at=due_at,
            bank_account_id=bank_account_id,
            bank_account_version=bank_account_version,
            package_price_version=package_price_version
        )
        self.db.add(invoice)
        self.db.commit()
        self.db.refresh(invoice)
        return invoice
