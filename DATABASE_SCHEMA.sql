-- DATABASE_SCHEMA.sql
-- Autonomous AI CS Engine for Travel Umroh
-- Production-oriented PostgreSQL schema with pgvector.

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

-- =========================
-- ENUMS
-- =========================

DO $$ BEGIN
  CREATE TYPE channel_type AS ENUM ('starsender', 'waba', 'telegram', 'web_widget', 'terminal');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
  CREATE TYPE conversation_status AS ENUM ('bot_active', 'collecting_booking', 'waiting_payment_evidence', 'handover_required', 'human_active', 'abuse_limited', 'closed');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
  CREATE TYPE message_direction AS ENUM ('incoming', 'outgoing');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
  CREATE TYPE message_type AS ENUM ('text', 'image', 'file', 'audio', 'location', 'system');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
  CREATE TYPE lead_stage AS ENUM ('new_lead', 'engaged', 'interested_package', 'booking_intent', 'data_collection', 'invoice_sent', 'payment_evidence_received', 'manual_verification', 'paid', 'lost', 'closed', 'abuse_limited');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
  CREATE TYPE booking_status AS ENUM ('draft', 'waiting_invoice', 'invoice_sent', 'waiting_payment', 'manual_checking', 'confirmed', 'cancelled', 'closed');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
  CREATE TYPE payment_status AS ENUM ('unpaid', 'evidence_received', 'manual_checking', 'paid', 'invalid', 'cancelled', 'expired', 'admin_overdue');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
  CREATE TYPE passenger_status AS ENUM ('placeholder', 'partial', 'complete', 'admin_required');
EXCEPTION WHEN duplicate_object THEN null; END $$;

DO $$ BEGIN
  CREATE TYPE queue_status AS ENUM ('pending', 'processing', 'done', 'failed', 'dead_letter');
EXCEPTION WHEN duplicate_object THEN null; END $$;

-- =========================
-- CLIENTS & CONFIG
-- =========================

CREATE TABLE IF NOT EXISTS clients (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_code VARCHAR(80) UNIQUE NOT NULL,
  name VARCHAR(180) NOT NULL,
  brand_name VARCHAR(180) NOT NULL,
  timezone VARCHAR(80) NOT NULL DEFAULT 'Asia/Jakarta',
  status VARCHAR(40) NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS client_configs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  config_key VARCHAR(120) NOT NULL,
  config_value JSONB NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, config_key, version)
);

CREATE TABLE IF NOT EXISTS channel_accounts (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  channel channel_type NOT NULL,
  account_identifier VARCHAR(180) NOT NULL,
  display_name VARCHAR(180),
  credential_ref VARCHAR(255),
  webhook_secret_ref VARCHAR(255),
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, channel, account_identifier)
);

CREATE TABLE IF NOT EXISTS admin_users (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  name VARCHAR(180) NOT NULL,
  phone VARCHAR(40),
  telegram_id VARCHAR(80),
  role VARCHAR(60) NOT NULL DEFAULT 'admin',
  status VARCHAR(40) NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =========================
-- WEBHOOK & MESSAGE CAPTURE
-- =========================

CREATE TABLE IF NOT EXISTS raw_webhook_payloads (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  channel channel_type NOT NULL,
  channel_account_id UUID REFERENCES channel_accounts(id),
  payload JSONB NOT NULL,
  headers JSONB,
  payload_hash VARCHAR(128),
  received_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS webhook_idempotency_keys (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  channel channel_type NOT NULL,
  idempotency_key VARCHAR(255) NOT NULL,
  event_type VARCHAR(80) NOT NULL,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  duplicate_count INTEGER NOT NULL DEFAULT 0,
  raw_payload_hash VARCHAR(128),
  related_message_id UUID,
  expires_at TIMESTAMPTZ NOT NULL,
  UNIQUE (client_id, channel, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_webhook_idempotency_expires_at ON webhook_idempotency_keys(expires_at);

CREATE TABLE IF NOT EXISTS contacts (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  phone_e164 VARCHAR(40) NOT NULL,
  display_name VARCHAR(180),
  city VARCHAR(120),
  source_channel channel_type,
  consent_status VARCHAR(40) DEFAULT 'unknown',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, phone_e164)
);

CREATE TABLE IF NOT EXISTS lead_profiles (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  stage lead_stage NOT NULL DEFAULT 'new_lead',
  interested_package_id UUID,
  budget_min INTEGER,
  budget_max INTEGER,
  pax_estimate INTEGER,
  departure_month_interest VARCHAR(40),
  tags TEXT[] NOT NULL DEFAULT '{}',
  last_intent VARCHAR(100),
  last_engaged_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, contact_id)
);

CREATE TABLE IF NOT EXISTS conversations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  channel_account_id UUID REFERENCES channel_accounts(id),
  channel channel_type NOT NULL,
  status conversation_status NOT NULL DEFAULT 'bot_active',
  bot_enabled BOOLEAN NOT NULL DEFAULT true,
  assigned_admin_id UUID REFERENCES admin_users(id),
  summary TEXT,
  summary_updated_at TIMESTAMPTZ,
  last_message_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_conversations_client_contact ON conversations(client_id, contact_id);
CREATE INDEX IF NOT EXISTS idx_conversations_status ON conversations(client_id, status);

CREATE TABLE IF NOT EXISTS messages (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  contact_id UUID REFERENCES contacts(id),
  raw_payload_id UUID REFERENCES raw_webhook_payloads(id),
  direction message_direction NOT NULL,
  sender_type VARCHAR(40) NOT NULL DEFAULT 'user',
  message_type message_type NOT NULL,
  text_content TEXT,
  file_url TEXT,
  file_path TEXT,
  content_hash VARCHAR(128),
  provider_message_id VARCHAR(255),
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_messages_conversation_created ON messages(conversation_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_client_created ON messages(client_id, created_at DESC);

CREATE TABLE IF NOT EXISTS intent_results (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
  intent VARCHAR(100) NOT NULL,
  confidence NUMERIC(5,4) NOT NULL,
  entities JSONB NOT NULL DEFAULT '{}',
  missing_fields TEXT[] NOT NULL DEFAULT '{}',
  model_name VARCHAR(120),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =========================
-- PACKAGE DATA
-- =========================

CREATE TABLE IF NOT EXISTS packages (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  package_code VARCHAR(80) NOT NULL,
  package_name VARCHAR(180) NOT NULL,
  description TEXT,
  duration_days INTEGER,
  price_per_pax INTEGER NOT NULL,
  currency VARCHAR(10) NOT NULL DEFAULT 'IDR',
  price_version INTEGER NOT NULL DEFAULT 1,
  quota INTEGER,
  remaining_seat INTEGER,
  status VARCHAR(40) NOT NULL DEFAULT 'active',
  effective_from TIMESTAMPTZ DEFAULT now(),
  effective_until TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, package_code, price_version)
);

CREATE TABLE IF NOT EXISTS departures (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  package_id UUID NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
  departure_date DATE NOT NULL,
  return_date DATE,
  quota INTEGER,
  remaining_seat INTEGER,
  status VARCHAR(40) NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS package_hotels (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  package_id UUID NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
  city VARCHAR(120) NOT NULL,
  hotel_name VARCHAR(180) NOT NULL,
  star_rating VARCHAR(20),
  distance_to_haram VARCHAR(120),
  nights INTEGER,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS package_airlines (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  package_id UUID NOT NULL REFERENCES packages(id) ON DELETE CASCADE,
  airline_name VARCHAR(180) NOT NULL,
  flight_route TEXT,
  baggage_info TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =========================
-- KNOWLEDGE BASE + RAG
-- =========================

CREATE TABLE IF NOT EXISTS knowledge_documents (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  title VARCHAR(220) NOT NULL,
  source_type VARCHAR(80) NOT NULL,
  file_path TEXT,
  doc_version VARCHAR(40) NOT NULL DEFAULT '1',
  doc_priority INTEGER NOT NULL DEFAULT 50,
  is_active BOOLEAN NOT NULL DEFAULT true,
  effective_from TIMESTAMPTZ DEFAULT now(),
  effective_until TIMESTAMPTZ,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS knowledge_chunks (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  document_id UUID NOT NULL REFERENCES knowledge_documents(id) ON DELETE CASCADE,
  chunk_index INTEGER NOT NULL,
  chunk_text TEXT NOT NULL,
  embedding vector(1536),
  token_count INTEGER,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (document_id, chunk_index)
);

CREATE INDEX IF NOT EXISTS idx_knowledge_chunks_client ON knowledge_chunks(client_id);

CREATE TABLE IF NOT EXISTS rag_response_cache (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  cache_key VARCHAR(180) NOT NULL,
  normalized_query TEXT NOT NULL,
  knowledge_version VARCHAR(80) NOT NULL,
  answer_text TEXT NOT NULL,
  confidence NUMERIC(5,4) NOT NULL,
  sources JSONB NOT NULL DEFAULT '[]',
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, cache_key)
);

CREATE INDEX IF NOT EXISTS idx_rag_response_cache_expires ON rag_response_cache(client_id, expires_at);

CREATE TABLE IF NOT EXISTS rag_source_traces (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  message_id UUID REFERENCES messages(id) ON DELETE SET NULL,
  answer_type VARCHAR(60) NOT NULL DEFAULT 'rag_answer',
  normalized_query TEXT NOT NULL,
  confidence NUMERIC(5,4) NOT NULL,
  knowledge_version VARCHAR(80) NOT NULL,
  sources JSONB NOT NULL DEFAULT '[]',
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_rag_source_traces_message ON rag_source_traces(client_id, message_id);

CREATE TABLE IF NOT EXISTS knowledge_conflict_logs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES conversations(id) ON DELETE SET NULL,
  query_text TEXT,
  conflict_type VARCHAR(80) NOT NULL,
  conflict_fields TEXT[] NOT NULL DEFAULT '{}',
  sources JSONB NOT NULL DEFAULT '[]',
  resolution VARCHAR(80) NOT NULL DEFAULT 'handover_required',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_knowledge_conflict_logs_client ON knowledge_conflict_logs(client_id, created_at DESC);

-- =========================
-- BOOKING, PASSENGERS, INVOICE
-- =========================

CREATE TABLE IF NOT EXISTS bookings (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES conversations(id),
  contact_id UUID NOT NULL REFERENCES contacts(id),
  package_id UUID NOT NULL REFERENCES packages(id),
  customer_name VARCHAR(180) NOT NULL,
  customer_phone VARCHAR(40) NOT NULL,
  pax INTEGER NOT NULL,
  total_amount INTEGER NOT NULL,
  status booking_status NOT NULL DEFAULT 'draft',
  passenger_collection_mode VARCHAR(40) NOT NULL DEFAULT 'bot_limited',
  group_notes TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS passengers (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  booking_id UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  passenger_order INTEGER NOT NULL,
  full_name VARCHAR(180),
  phone VARCHAR(40),
  gender VARCHAR(30),
  birth_date DATE,
  status passenger_status NOT NULL DEFAULT 'placeholder',
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (booking_id, passenger_order)
);

CREATE TABLE IF NOT EXISTS bank_accounts (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  bank_name VARCHAR(120) NOT NULL,
  account_number VARCHAR(80) NOT NULL,
  account_holder VARCHAR(180) NOT NULL,
  version INTEGER NOT NULL DEFAULT 1,
  is_active BOOLEAN NOT NULL DEFAULT true,
  effective_from TIMESTAMPTZ NOT NULL DEFAULT now(),
  effective_until TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, account_number, version)
);

CREATE TABLE IF NOT EXISTS invoices (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  booking_id UUID NOT NULL REFERENCES bookings(id) ON DELETE CASCADE,
  invoice_number VARCHAR(120) NOT NULL,
  revision_number INTEGER NOT NULL DEFAULT 1,
  amount INTEGER NOT NULL,
  currency VARCHAR(10) NOT NULL DEFAULT 'IDR',
  pdf_path TEXT NOT NULL,
  payment_status payment_status NOT NULL DEFAULT 'unpaid',
  due_at TIMESTAMPTZ NOT NULL,
  bank_account_id UUID REFERENCES bank_accounts(id),
  bank_account_version INTEGER NOT NULL,
  package_price_version INTEGER NOT NULL,
  created_by VARCHAR(80) NOT NULL DEFAULT 'system',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (client_id, invoice_number, revision_number)
);

CREATE INDEX IF NOT EXISTS idx_invoices_due_status ON invoices(client_id, payment_status, due_at);

CREATE TABLE IF NOT EXISTS payment_evidences (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  invoice_id UUID REFERENCES invoices(id) ON DELETE SET NULL,
  conversation_id UUID REFERENCES conversations(id),
  message_id UUID REFERENCES messages(id),
  file_path TEXT,
  file_url TEXT,
  caption TEXT,
  triage_status VARCHAR(60) NOT NULL DEFAULT 'likely_payment_evidence',
  admin_validation_status payment_status NOT NULL DEFAULT 'evidence_received',
  validated_by_admin_id UUID REFERENCES admin_users(id),
  validated_at TIMESTAMPTZ,
  admin_note TEXT,
  uploaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =========================
-- HANDOVER & FOLLOW-UP
-- =========================

CREATE TABLE IF NOT EXISTS handover_events (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  contact_id UUID REFERENCES contacts(id),
  reason VARCHAR(100) NOT NULL,
  status VARCHAR(60) NOT NULL DEFAULT 'open',
  assigned_admin_id UUID REFERENCES admin_users(id),
  summary TEXT,
  triggered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  resolved_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS followup_tasks (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID NOT NULL REFERENCES clients(id) ON DELETE CASCADE,
  contact_id UUID NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
  conversation_id UUID REFERENCES conversations(id),
  booking_id UUID REFERENCES bookings(id),
  invoice_id UUID REFERENCES invoices(id),
  trigger_event VARCHAR(100) NOT NULL,
  template_key VARCHAR(120) NOT NULL,
  scheduled_at TIMESTAMPTZ NOT NULL,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  status queue_status NOT NULL DEFAULT 'pending',
  stopped_reason VARCHAR(120),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =========================
-- QUEUE, HEALTH, AUDIT
-- =========================

CREATE TABLE IF NOT EXISTS queue_jobs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  queue_name VARCHAR(120) NOT NULL,
  task_name VARCHAR(120) NOT NULL,
  idempotency_key VARCHAR(255),
  payload JSONB NOT NULL DEFAULT '{}',
  status queue_status NOT NULL DEFAULT 'pending',
  attempt_count INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  next_run_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_error TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (queue_name, idempotency_key)
);

CREATE TABLE IF NOT EXISTS dead_letter_jobs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  original_job_id UUID,
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  queue_name VARCHAR(120) NOT NULL,
  task_name VARCHAR(120) NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}',
  error_message TEXT,
  failed_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS channel_health_checks (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  channel channel_type NOT NULL,
  status VARCHAR(40) NOT NULL,
  can_send_message BOOLEAN,
  can_receive_webhook BOOLEAN,
  credential_valid BOOLEAN,
  latency_ms INTEGER,
  error_code VARCHAR(120),
  recommended_action TEXT,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_logs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  client_id UUID REFERENCES clients(id) ON DELETE CASCADE,
  actor_type VARCHAR(40) NOT NULL DEFAULT 'system',
  actor_id UUID,
  event_type VARCHAR(120) NOT NULL,
  entity_type VARCHAR(120),
  entity_id UUID,
  old_value JSONB,
  new_value JSONB,
  metadata JSONB NOT NULL DEFAULT '{}',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- =========================
-- IMPORTANT INDEXES
-- =========================

CREATE INDEX IF NOT EXISTS idx_contacts_client_phone ON contacts(client_id, phone_e164);
CREATE INDEX IF NOT EXISTS idx_lead_profiles_client_stage ON lead_profiles(client_id, stage);
CREATE INDEX IF NOT EXISTS idx_bookings_client_status ON bookings(client_id, status);
CREATE INDEX IF NOT EXISTS idx_payment_evidences_client_invoice ON payment_evidences(client_id, invoice_id);
CREATE INDEX IF NOT EXISTS idx_followup_tasks_due ON followup_tasks(client_id, status, scheduled_at);
CREATE INDEX IF NOT EXISTS idx_queue_jobs_due ON queue_jobs(status, next_run_at);
CREATE INDEX IF NOT EXISTS idx_audit_logs_entity ON audit_logs(client_id, entity_type, entity_id, created_at DESC);
