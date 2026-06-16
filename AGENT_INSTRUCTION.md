# AGENT_INSTRUCTION.md
## Master Instruction for AI Coding Agent

You are building a production-ready backend for **Autonomous AI CS Engine for Travel Umroh**.

This system is not a toy chatbot. It is a WhatsApp-based AI customer service, lead database, RAG assistant, booking collector, invoice generator, and manual-payment handover engine for travel umroh businesses. Build it as a modular system that can start with Starsender and later switch to WABA without rewriting core logic.

---

## 1. Non-Negotiable Principles

1. **Payment verification is manual only.**
   - AI, worker, OCR, webhook, or payment parser must never mark invoice as `paid`.
   - Only an authenticated admin/operator may set `payment_status = paid`.

2. **Backend is the source of truth.**
   - LLM may extract intent and generate conversational replies.
   - Backend calculates price, invoice total, status transition, bank account version, and booking state.

3. **All WhatsApp chats must be captured to database.**
   - Do not rely on WhatsApp chat history as the database.
   - Every incoming and outgoing message must be stored.

4. **Starsender is an adapter, not the system.**
   - Core services must not import Starsender-specific logic.
   - WABA must be replaceable through adapter + config.

5. **Multi-client isolation is mandatory.**
   - Every tenant-owned table must include `client_id`.
   - Every query must filter by `client_id`.
   - RAG retrieval must filter by `client_id` and active knowledge version.

6. **No hardcoded client data.**
   - Bank account, package data, prompt, tone, admin group, and channel credentials come from config/database.

7. **Do not overbuild dashboard first.**
   - Build ingestion, database, RAG, AI safety, booking, invoice, and handover first.
   - Dashboard is secondary.

---

## 2. Required Build Order

Build in this exact order. Do not skip to prettier modules because computers apparently enjoy distracting humans.

### Step 1 - Project Skeleton
Create:

```text
app/
  main.py
  config.py
  storage/
  channels/
  core/
  ai/
  rag/
  booking/
  payment/
  handover/
  followup/
  workers/
  utils/
clients/
  travel_alfalah/
    config/
    knowledge/
    invoices/
    uploads/
tests/
```

Acceptance:
- App boots with `.env`.
- Config loader can load global config and client config.

---

### Step 2 - Database Schema & Migrations
Use `DATABASE_SCHEMA.sql` as the canonical schema source.

Acceptance:
- Fresh PostgreSQL database can be migrated from zero.
- `pgvector` extension is enabled.
- Core tables exist.
- `client_id` exists in all tenant-owned tables.
- Money fields are integer, not float.
- Time fields use `TIMESTAMPTZ`.

---

### Step 3 - Channel Adapter Interface
Create abstract interface:

```python
class ChannelAdapter:
    def parse_incoming(self, payload: dict) -> MessageEvent: ...
    def send_text(self, conversation_id: str, text: str) -> SendResult: ...
    def send_file(self, conversation_id: str, file_path: str, caption: str | None = None) -> SendResult: ...
    def mark_read(self, conversation_id: str) -> None: ...
    def get_sender_identity(self, payload: dict) -> SenderIdentity: ...
    def validate_signature(self, payload: dict, headers: dict) -> bool: ...
    def health_check(self) -> ChannelHealthResult: ...
```

Acceptance:
- Starsender adapter implements this interface.
- WABA adapter skeleton exists with TODOs where credentials are unavailable.
- Core router only receives normalized `MessageEvent`.

---

### Step 4 - Idempotency + Message Capture
Before processing any webhook:

1. Build `idempotency_key`.
2. Insert into `webhook_idempotency_keys` with unique constraint.
3. If key exists, increment duplicate count and stop processing.
4. Save raw webhook payload.
5. Save normalized message.

Acceptance:
- Duplicate webhook does not create duplicate message.
- Duplicate webhook does not trigger duplicate AI reply.
- Duplicate webhook does not generate duplicate invoice.

---

### Step 5 - Contact, Lead, Conversation State
Create or update:
- Contact
- Lead profile
- Conversation
- Message log

Acceptance:
- Every incoming WhatsApp number maps to one contact per client.
- Conversation status controls whether bot may reply.
- If status is `handover_required`, `human_active`, or `abuse_limited`, bot must not auto-reply.

---

### Step 6 - RAG System
Use PostgreSQL + pgvector.

Acceptance:
- Knowledge documents can be ingested.
- Chunks have embeddings.
- Retrieval filters by `client_id`.
- Retrieval has score threshold.
- Knowledge conflict resolution uses priority rules.
- If context is missing or conflicting, system falls back to admin.

---

### Step 7 - LLM Wrapper + AI Config Validation
Read `ai_config.json`.

Acceptance:
- Provider base URL and models come from config.
- Startup performs lightweight model validation.
- If model string is rejected in production, service fails safe.
- LLM request uses sliding memory + summary, not full chat history.

---

### Step 8 - Intent, Entity, Safety Guard
Acceptance:
- System detects package questions, price questions, booking intent, payment evidence, human request, complaint, and unknown intent.
- Safety guard blocks forbidden payment confirmation.
- AI cannot mention unregistered bank account.
- AI cannot invent package, price, hotel, date, airline, or quota.

---

### Step 9 - Booking + Multi-Pax Rules
Acceptance:
- 1 pax booking works.
- 2-5 pax booking creates passenger placeholders.
- 6-10 pax stores group summary and hands passenger detail to admin.
- >10 pax triggers handover.
- Booking total is calculated by backend.

---

### Step 10 - Invoice Generation
Acceptance:
- Invoice PDF is generated from backend data.
- Invoice number is unique and immutable.
- Invoice stores `bank_account_version` and `price_version`.
- Old invoice PDFs are not overwritten.

---

### Step 11 - Payment Evidence + Handover
Acceptance:
- Evidence is saved.
- Bot acknowledges receipt without validation.
- Conversation locks.
- Admin receives notification.
- Only admin can mark paid/invalid/cancelled.

---

### Step 12 - Workers
Implement queues for:
- message retry
- invoice generation
- admin notification
- summary generation
- follow-up scheduler
- invoice expiry scanner
- dead-letter logging

Acceptance:
- Tasks are idempotent.
- Failed tasks retry with backoff.
- Dead-letter table receives permanently failed jobs.

---

## 3. Output Expected From Agent

The final generated project must include:

```text
README.md
.env.example
docker-compose.yml
alembic/ or migrations/
app/
clients/travel_alfalah/sample configs
tests/
```

Also include:
- setup instruction
- local run instruction
- test command
- migration command
- known limitations

---

## 4. Forbidden Agent Behavior

Do not:
- Mark payment as paid automatically.
- Hardcode bank account in code.
- Hardcode client package data in prompt.
- Put Starsender logic inside booking, payment, invoice, RAG, or AI service.
- Query tenant-owned data without `client_id`.
- Store money as float.
- Send full chat history to LLM.
- Overwrite generated invoice PDF.
- Ignore idempotency.
- Build dashboard before ingestion and database capture.

---

## 5. Final Rule

If PRD and generated code conflict, PRD wins.
If PRD feels ambiguous, use Section 38 of the PRD.
If still ambiguous, add a clear TODO and stop that module rather than inventing production behavior.
