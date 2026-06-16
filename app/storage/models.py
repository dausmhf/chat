import uuid
from sqlalchemy import Column, String, Integer, Boolean, DateTime, ForeignKey, Date, Numeric, Text
from sqlalchemy.types import TypeDecorator, JSON as SqliteJSON
from sqlalchemy.dialects.postgresql import JSONB as PostgresJSONB, ARRAY as PostgresARRAY, ENUM as PostgresENUM
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.storage.database import Base

class SafeJSONB(TypeDecorator):
    impl = SqliteJSON
    cache_ok = True
    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PostgresJSONB)
        else:
            return dialect.type_descriptor(SqliteJSON)

class SafeARRAY(TypeDecorator):
    impl = SqliteJSON
    cache_ok = True
    def __init__(self, item_type, *args, **kwargs):
        self.item_type = item_type
        super().__init__(*args, **kwargs)
    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PostgresARRAY(self.item_type))
        else:
            return dialect.type_descriptor(SqliteJSON)

class SafeVector(TypeDecorator):
    impl = SqliteJSON
    cache_ok = True
    def __init__(self, dim, *args, **kwargs):
        self.dim = dim
        super().__init__(*args, **kwargs)
    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            try:
                from pgvector.sqlalchemy import Vector
                return dialect.type_descriptor(Vector(self.dim))
            except ImportError:
                return dialect.type_descriptor(SqliteJSON)
        else:
            return dialect.type_descriptor(SqliteJSON)

class SafeEnum(TypeDecorator):
    impl = String(40)
    cache_ok = True
    def __init__(self, enum_name, values, *args, **kwargs):
        self.enum_name = enum_name
        self.values = values
        super().__init__(*args, **kwargs)
    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PostgresENUM(*self.values, name=self.enum_name, create_type=False))
        return dialect.type_descriptor(String(40))

CHANNEL_TYPES = ("starsender", "waba", "telegram", "web_widget", "terminal")
CONVERSATION_STATUSES = ("bot_active", "collecting_booking", "waiting_payment_evidence", "handover_required", "human_active", "abuse_limited", "closed")
MESSAGE_DIRECTIONS = ("incoming", "outgoing")
MESSAGE_TYPES = ("text", "image", "file", "audio", "location", "system")
LEAD_STAGES = ("new_lead", "engaged", "interested_package", "booking_intent", "data_collection", "invoice_sent", "payment_evidence_received", "manual_verification", "paid", "lost", "closed", "abuse_limited")
BOOKING_STATUSES = ("draft", "waiting_invoice", "invoice_sent", "waiting_payment", "manual_checking", "confirmed", "cancelled", "closed")
PAYMENT_STATUSES = ("unpaid", "evidence_received", "manual_checking", "paid", "invalid", "cancelled", "expired", "admin_overdue")
PASSENGER_STATUSES = ("placeholder", "partial", "complete", "admin_required")
QUEUE_STATUSES = ("pending", "processing", "done", "failed", "dead_letter")

class SafeUUID(TypeDecorator):
    impl = String(36)
    cache_ok = True
    def __init__(self, *args, **kwargs):
        kwargs.pop("as_uuid", None)
        super().__init__(*args, **kwargs)
    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import UUID as PostgresUUID
            return dialect.type_descriptor(PostgresUUID(as_uuid=True))
        else:
            return dialect.type_descriptor(String(36))
    def process_bind_param(self, value, dialect):
        if value is not None:
            if dialect.name == "postgresql":
                if isinstance(value, str):
                    return uuid.UUID(value)
                return value
            else:
                return str(value)
        return None
    def process_result_value(self, value, dialect):
        if value is not None:
            if isinstance(value, uuid.UUID):
                return value
            return uuid.UUID(value)
        return None

class Client(Base):
    __tablename__ = "clients"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_code = Column(String(80), unique=True, nullable=False)
    name = Column(String(180), nullable=False)
    brand_name = Column(String(180), nullable=False)
    timezone = Column(String(80), nullable=False, default="Asia/Jakarta")
    status = Column(String(40), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class ClientConfig(Base):
    __tablename__ = "client_configs"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    config_key = Column(String(120), nullable=False)
    config_value = Column(SafeJSONB, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class ChannelAccount(Base):
    __tablename__ = "channel_accounts"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    channel = Column(SafeEnum("channel_type", CHANNEL_TYPES), nullable=False)
    account_identifier = Column(String(180), nullable=False)
    display_name = Column(String(180))
    credential_ref = Column(String(255))
    webhook_secret_ref = Column(String(255))
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class AdminUser(Base):
    __tablename__ = "admin_users"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(180), nullable=False)
    phone = Column(String(40))
    telegram_id = Column(String(80))
    role = Column(String(60), nullable=False, default="admin")
    status = Column(String(40), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class RawWebhookPayload(Base):
    __tablename__ = "raw_webhook_payloads"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    channel = Column(SafeEnum("channel_type", CHANNEL_TYPES), nullable=False)
    channel_account_id = Column(SafeUUID, ForeignKey("channel_accounts.id"), nullable=True)
    payload = Column(SafeJSONB, nullable=False)
    headers = Column(SafeJSONB, nullable=True)
    payload_hash = Column(String(128), nullable=True)
    received_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class WebhookIdempotencyKey(Base):
    __tablename__ = "webhook_idempotency_keys"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    channel = Column(SafeEnum("channel_type", CHANNEL_TYPES), nullable=False)
    idempotency_key = Column(String(255), nullable=False)
    event_type = Column(String(80), nullable=False)
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    duplicate_count = Column(Integer, nullable=False, default=0)
    raw_payload_hash = Column(String(128), nullable=True)
    related_message_id = Column(SafeUUID, nullable=True)
    expires_at = Column(DateTime(timezone=True), nullable=False)

class Contact(Base):
    __tablename__ = "contacts"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    phone_e164 = Column(String(40), nullable=False)
    display_name = Column(String(180))
    city = Column(String(120))
    source_channel = Column(SafeEnum("channel_type", CHANNEL_TYPES))
    consent_status = Column(String(40), default="unknown")
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class LeadProfile(Base):
    __tablename__ = "lead_profiles"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(SafeUUID, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    stage = Column(SafeEnum("lead_stage", LEAD_STAGES), nullable=False, default="new_lead")
    interested_package_id = Column(SafeUUID, nullable=True)
    budget_min = Column(Integer, nullable=True)
    budget_max = Column(Integer, nullable=True)
    pax_estimate = Column(Integer, nullable=True)
    departure_month_interest = Column(String(40), nullable=True)
    tags = Column(SafeARRAY(Text), nullable=False, default=[])
    last_intent = Column(String(100), nullable=True)
    last_engaged_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class Conversation(Base):
    __tablename__ = "conversations"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(SafeUUID, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    channel_account_id = Column(SafeUUID, ForeignKey("channel_accounts.id"), nullable=True)
    channel = Column(SafeEnum("channel_type", CHANNEL_TYPES), nullable=False)
    status = Column(SafeEnum("conversation_status", CONVERSATION_STATUSES), nullable=False, default="bot_active")
    bot_enabled = Column(Boolean, nullable=False, default=True)
    assigned_admin_id = Column(SafeUUID, ForeignKey("admin_users.id"), nullable=True)
    summary = Column(Text, nullable=True)
    summary_updated_at = Column(DateTime(timezone=True), nullable=True)
    last_message_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class Message(Base):
    __tablename__ = "messages"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(SafeUUID, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(SafeUUID, ForeignKey("contacts.id"), nullable=True)
    raw_payload_id = Column(SafeUUID, ForeignKey("raw_webhook_payloads.id"), nullable=True)
    direction = Column(SafeEnum("message_direction", MESSAGE_DIRECTIONS), nullable=False)
    sender_type = Column(String(40), nullable=False, default="user")
    message_type = Column(SafeEnum("message_type", MESSAGE_TYPES), nullable=False)
    text_content = Column(Text, nullable=True)
    file_url = Column(Text, nullable=True)
    file_path = Column(Text, nullable=True)
    content_hash = Column(String(128), nullable=True)
    provider_message_id = Column(String(255), nullable=True)
    meta_data = Column("metadata", SafeJSONB, nullable=False, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class IntentResult(Base):
    __tablename__ = "intent_results"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(SafeUUID, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(SafeUUID, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    intent = Column(String(100), nullable=False)
    confidence = Column(Numeric(5, 4), nullable=False)
    entities = Column(SafeJSONB, nullable=False, default={})
    missing_fields = Column(SafeARRAY(Text), nullable=False, default=[])
    model_name = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class Package(Base):
    __tablename__ = "packages"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    package_code = Column(String(80), nullable=False)
    package_name = Column(String(180), nullable=False)
    description = Column(Text, nullable=True)
    duration_days = Column(Integer, nullable=True)
    price_per_pax = Column(Integer, nullable=False)
    currency = Column(String(10), nullable=False, default="IDR")
    price_version = Column(Integer, nullable=False, default=1)
    quota = Column(Integer, nullable=True)
    remaining_seat = Column(Integer, nullable=True)
    status = Column(String(40), nullable=False, default="active")
    effective_from = Column(DateTime(timezone=True), default=func.now())
    effective_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class Departure(Base):
    __tablename__ = "departures"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    package_id = Column(SafeUUID, ForeignKey("packages.id", ondelete="CASCADE"), nullable=False)
    departure_date = Column(Date, nullable=False)
    return_date = Column(Date, nullable=True)
    quota = Column(Integer, nullable=True)
    remaining_seat = Column(Integer, nullable=True)
    status = Column(String(40), nullable=False, default="active")
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class PackageHotel(Base):
    __tablename__ = "package_hotels"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    package_id = Column(SafeUUID, ForeignKey("packages.id", ondelete="CASCADE"), nullable=False)
    city = Column(String(120), nullable=False)
    hotel_name = Column(String(180), nullable=False)
    star_rating = Column(String(20), nullable=True)
    distance_to_haram = Column(String(120), nullable=True)
    nights = Column(Integer, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class PackageAirline(Base):
    __tablename__ = "package_airlines"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    package_id = Column(SafeUUID, ForeignKey("packages.id", ondelete="CASCADE"), nullable=False)
    airline_name = Column(String(180), nullable=False)
    flight_route = Column(Text, nullable=True)
    baggage_info = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(220), nullable=False)
    source_type = Column(String(80), nullable=False)
    file_path = Column(Text, nullable=True)
    doc_version = Column(String(40), nullable=False, default="1")
    doc_priority = Column(Integer, nullable=False, default=50)
    is_active = Column(Boolean, nullable=False, default=True)
    effective_from = Column(DateTime(timezone=True), default=func.now())
    effective_until = Column(DateTime(timezone=True), nullable=True)
    meta_data = Column("metadata", SafeJSONB, nullable=False, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    document_id = Column(SafeUUID, ForeignKey("knowledge_documents.id", ondelete="CASCADE"), nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)
    embedding = Column(SafeVector(1536), nullable=True)
    token_count = Column(Integer, nullable=True)
    meta_data = Column("metadata", SafeJSONB, nullable=False, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class RagResponseCache(Base):
    __tablename__ = "rag_response_cache"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    cache_key = Column(String(180), nullable=False)
    normalized_query = Column(Text, nullable=False)
    knowledge_version = Column(String(80), nullable=False)
    answer_text = Column(Text, nullable=False)
    confidence = Column(Numeric(5, 4), nullable=False)
    sources = Column(SafeJSONB, nullable=False, default=[])
    expires_at = Column(DateTime(timezone=True), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class RagSourceTrace(Base):
    __tablename__ = "rag_source_traces"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(SafeUUID, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    message_id = Column(SafeUUID, ForeignKey("messages.id", ondelete="SET NULL"), nullable=True)
    answer_type = Column(String(60), nullable=False, default="rag_answer")
    normalized_query = Column(Text, nullable=False)
    confidence = Column(Numeric(5, 4), nullable=False)
    knowledge_version = Column(String(80), nullable=False)
    sources = Column(SafeJSONB, nullable=False, default=[])
    meta_data = Column("metadata", SafeJSONB, nullable=False, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class KnowledgeConflictLog(Base):
    __tablename__ = "knowledge_conflict_logs"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(SafeUUID, ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True)
    query_text = Column(Text, nullable=True)
    conflict_type = Column(String(80), nullable=False)
    conflict_fields = Column(SafeARRAY(Text), nullable=False, default=[])
    sources = Column(SafeJSONB, nullable=False, default=[])
    resolution = Column(String(80), nullable=False, default="handover_required")
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class Booking(Base):
    __tablename__ = "bookings"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(SafeUUID, ForeignKey("conversations.id"), nullable=True)
    contact_id = Column(SafeUUID, ForeignKey("contacts.id"), nullable=False)
    package_id = Column(SafeUUID, ForeignKey("packages.id"), nullable=False)
    customer_name = Column(String(180), nullable=False)
    customer_phone = Column(String(40), nullable=False)
    pax = Column(Integer, nullable=False)
    total_amount = Column(Integer, nullable=False)
    status = Column(SafeEnum("booking_status", BOOKING_STATUSES), nullable=False, default="draft")
    passenger_collection_mode = Column(String(40), nullable=False, default="bot_limited")
    group_notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class Passenger(Base):
    __tablename__ = "passengers"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    booking_id = Column(SafeUUID, ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False)
    passenger_order = Column(Integer, nullable=False)
    full_name = Column(String(180), nullable=True)
    phone = Column(String(40), nullable=True)
    gender = Column(String(30), nullable=True)
    birth_date = Column(Date, nullable=True)
    status = Column(SafeEnum("passenger_status", PASSENGER_STATUSES), nullable=False, default="placeholder")
    meta_data = Column("metadata", SafeJSONB, nullable=False, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class BankAccount(Base):
    __tablename__ = "bank_accounts"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    bank_name = Column(String(120), nullable=False)
    account_number = Column(String(80), nullable=False)
    account_holder = Column(String(180), nullable=False)
    version = Column(Integer, nullable=False, default=1)
    is_active = Column(Boolean, nullable=False, default=True)
    effective_from = Column(DateTime(timezone=True), nullable=False, default=func.now())
    effective_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class Invoice(Base):
    __tablename__ = "invoices"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    booking_id = Column(SafeUUID, ForeignKey("bookings.id", ondelete="CASCADE"), nullable=False)
    invoice_number = Column(String(120), nullable=False)
    revision_number = Column(Integer, nullable=False, default=1)
    amount = Column(Integer, nullable=False)
    currency = Column(String(10), nullable=False, default="IDR")
    pdf_path = Column(Text, nullable=False)
    payment_status = Column(SafeEnum("payment_status", PAYMENT_STATUSES), nullable=False, default="unpaid")
    due_at = Column(DateTime(timezone=True), nullable=False)
    bank_account_id = Column(SafeUUID, ForeignKey("bank_accounts.id"), nullable=True)
    bank_account_version = Column(Integer, nullable=False)
    package_price_version = Column(Integer, nullable=False)
    created_by = Column(String(80), nullable=False, default="system")
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class PaymentEvidence(Base):
    __tablename__ = "payment_evidences"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    invoice_id = Column(SafeUUID, ForeignKey("invoices.id", ondelete="SET NULL"), nullable=True)
    conversation_id = Column(SafeUUID, ForeignKey("conversations.id"), nullable=True)
    message_id = Column(SafeUUID, ForeignKey("messages.id"), nullable=True)
    file_path = Column(Text, nullable=True)
    file_url = Column(Text, nullable=True)
    caption = Column(Text, nullable=True)
    triage_status = Column(String(60), nullable=False, default="likely_payment_evidence")
    admin_validation_status = Column(SafeEnum("payment_status", PAYMENT_STATUSES), nullable=False, default="evidence_received")
    validated_by_admin_id = Column(SafeUUID, ForeignKey("admin_users.id"), nullable=True)
    validated_at = Column(DateTime(timezone=True), nullable=True)
    admin_note = Column(Text, nullable=True)
    uploaded_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class HandoverEvent(Base):
    __tablename__ = "handover_events"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(SafeUUID, ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(SafeUUID, ForeignKey("contacts.id"), nullable=True)
    reason = Column(String(100), nullable=False)
    status = Column(String(60), nullable=False, default="open")
    assigned_admin_id = Column(SafeUUID, ForeignKey("admin_users.id"), nullable=True)
    summary = Column(Text, nullable=True)
    triggered_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    resolved_at = Column(DateTime(timezone=True), nullable=True)

class FollowupTask(Base):
    __tablename__ = "followup_tasks"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(SafeUUID, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    conversation_id = Column(SafeUUID, ForeignKey("conversations.id"), nullable=True)
    booking_id = Column(SafeUUID, ForeignKey("bookings.id"), nullable=True)
    invoice_id = Column(SafeUUID, ForeignKey("invoices.id"), nullable=True)
    trigger_event = Column(String(100), nullable=False)
    template_key = Column(String(120), nullable=False)
    scheduled_at = Column(DateTime(timezone=True), nullable=False)
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    status = Column(SafeEnum("queue_status", QUEUE_STATUSES), nullable=False, default="pending")
    stopped_reason = Column(String(120), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class QueueJob(Base):
    __tablename__ = "queue_jobs"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True)
    queue_name = Column(String(120), nullable=False)
    task_name = Column(String(120), nullable=False)
    idempotency_key = Column(String(255), nullable=True)
    payload = Column(SafeJSONB, nullable=False, default={})
    status = Column(SafeEnum("queue_status", QUEUE_STATUSES), nullable=False, default="pending")
    attempt_count = Column(Integer, nullable=False, default=0)
    max_attempts = Column(Integer, nullable=False, default=3)
    next_run_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    last_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, default=func.now(), onupdate=func.now())

class DeadLetterJob(Base):
    __tablename__ = "dead_letter_jobs"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    original_job_id = Column(SafeUUID, nullable=True)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True)
    queue_name = Column(String(120), nullable=False)
    task_name = Column(String(120), nullable=False)
    payload = Column(SafeJSONB, nullable=False, default={})
    error_message = Column(Text, nullable=True)
    failed_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class ChannelHealthCheck(Base):
    __tablename__ = "channel_health_checks"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True)
    channel = Column(SafeEnum("channel_type", CHANNEL_TYPES), nullable=False)
    status = Column(String(40), nullable=False)
    can_send_message = Column(Boolean, nullable=True)
    can_receive_webhook = Column(Boolean, nullable=True)
    credential_valid = Column(Boolean, nullable=True)
    latency_ms = Column(Integer, nullable=True)
    error_code = Column(String(120), nullable=True)
    recommended_action = Column(Text, nullable=True)
    checked_at = Column(DateTime(timezone=True), nullable=False, default=func.now())

class AuditLog(Base):
    __tablename__ = "audit_logs"
    id = Column(SafeUUID, primary_key=True, default=uuid.uuid4)
    client_id = Column(SafeUUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=True)
    actor_type = Column(String(40), nullable=False, default="system")
    actor_id = Column(SafeUUID, nullable=True)
    event_type = Column(String(120), nullable=False)
    entity_type = Column(String(120), nullable=True)
    entity_id = Column(SafeUUID, nullable=True)
    old_value = Column(SafeJSONB, nullable=True)
    new_value = Column(SafeJSONB, nullable=True)
    meta_data = Column("metadata", SafeJSONB, nullable=False, default={})
    created_at = Column(DateTime(timezone=True), nullable=False, default=func.now())
