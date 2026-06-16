# IMPLEMENTATION_CHECKLIST.md
## Autonomous AI CS Engine for Travel Umroh

Use this checklist after coding each module. Do not mark a module complete just because the code runs once. That is how tiny bugs become invoice ghosts.

---

## 1. Project & Config

- [ ] `.env.example` exists.
- [ ] Global config loads correctly.
- [ ] Client config loads by `client_id`.
- [ ] Missing required config fails startup safely.
- [ ] Feature flags can enable/disable modules.
- [ ] No client data is hardcoded in source code.

---

## 2. Database

- [ ] PostgreSQL migration runs from fresh DB.
- [ ] `pgvector` extension is enabled.
- [ ] All tenant-owned tables include `client_id`.
- [ ] Core FK constraints exist.
- [ ] Money fields use integer.
- [ ] Time fields use `TIMESTAMPTZ`.
- [ ] Audit tables exist.
- [ ] Indexes exist for high-traffic lookups.

---

## 3. Channel Adapter

- [ ] `ChannelAdapter` base interface exists.
- [ ] Starsender adapter implements all methods.
- [ ] WABA adapter skeleton exists.
- [ ] Incoming payload normalizes to `MessageEvent`.
- [ ] `health_check()` returns structured status.
- [ ] Unhealthy adapter pauses auto-reply safely.

---

## 4. Idempotency

- [ ] `webhook_idempotency_keys` table exists.
- [ ] Unique constraint: `client_id + channel + idempotency_key`.
- [ ] Duplicate webhook returns OK without reprocessing.
- [ ] Duplicate webhook increments `duplicate_count`.
- [ ] Duplicate webhook does not generate duplicate AI reply.
- [ ] Duplicate webhook does not generate duplicate invoice.

---

## 5. Message Capture

- [ ] Raw webhook payload is saved.
- [ ] Normalized incoming message is saved.
- [ ] Outgoing bot/admin messages are saved.
- [ ] File/image metadata is saved.
- [ ] Contact is created or updated per client.
- [ ] Lead profile is created or updated per client.

---

## 6. Conversation State

- [ ] Conversation has `status`.
- [ ] Conversation has `bot_enabled`.
- [ ] Bot does not reply in `handover_required`.
- [ ] Bot does not reply in `human_active`.
- [ ] Bot does not reply in `abuse_limited`.
- [ ] Admin can return conversation to bot-active.

---

## 7. RAG

- [ ] Knowledge documents can be imported.
- [ ] Chunks are created with metadata.
- [ ] Embeddings are stored in pgvector.
- [ ] Retrieval filters by `client_id`.
- [ ] Retrieval filters active knowledge version.
- [ ] Retrieval threshold is enforced.
- [ ] Conflict resolution rules are implemented.
- [ ] Missing/conflicting data triggers fallback to admin.

---

## 8. AI/LLM

- [ ] `ai_config.json` schema is implemented.
- [ ] Model string validation runs at startup/deploy.
- [ ] LLM timeout is enforced.
- [ ] LLM retry is limited.
- [ ] Sliding memory limit is enforced.
- [ ] Summary is used instead of full chat history.
- [ ] Sensitive document data is not sent freely to LLM.

---

## 9. Safety Guard

- [ ] Blocks payment confirmation.
- [ ] Blocks unknown bank account.
- [ ] Blocks invented package/hotel/date/price/airline/quota.
- [ ] Blocks refund/cancellation promises outside terms.
- [ ] Handles low confidence.
- [ ] Logs blocked responses.
- [ ] Uses fallback response safely.

---

## 10. Booking

- [ ] Booking intent is detected.
- [ ] Required fields are collected: name, phone, package, pax.
- [ ] Backend calculates total price.
- [ ] Package status is checked before booking.
- [ ] Remaining seat rule is respected if data exists.
- [ ] Booking draft is saved.

---

## 11. Multi-Pax

- [ ] 1 pax flow works.
- [ ] 2-5 pax creates passenger placeholders.
- [ ] 6-10 pax stores group summary and asks admin to collect detail.
- [ ] >10 pax triggers group handover.
- [ ] Bot does not force long passenger form inside WhatsApp.

---

## 12. Invoice

- [ ] Invoice number is unique.
- [ ] Invoice PDF is generated.
- [ ] Invoice PDF is immutable.
- [ ] Invoice stores bank account version.
- [ ] Invoice stores price version.
- [ ] Revised invoice creates new record/file.
- [ ] Old invoice is not overwritten.

---

## 13. Payment Manual

- [ ] Bot can receive payment evidence.
- [ ] Evidence file is stored.
- [ ] Bot acknowledges without validation.
- [ ] Conversation locks after likely evidence.
- [ ] Admin is notified.
- [ ] Only admin can mark paid.
- [ ] Invalid evidence flow exists.
- [ ] Cancelled flow exists.

---

## 14. Invoice Expiry

- [ ] Worker scans due invoices every 15 minutes.
- [ ] `unpaid` invoice becomes `expired` after grace window.
- [ ] `evidence_received` does not auto-expire.
- [ ] `manual_checking` does not auto-expire.
- [ ] Admin receives batch notification.
- [ ] Expired invoice can be revised by admin.

---

## 15. Handover

- [ ] Payment evidence triggers handover.
- [ ] Human request triggers handover.
- [ ] Low confidence can trigger handover.
- [ ] Complaint can trigger handover.
- [ ] Admin can set `human_active`.
- [ ] Admin can return control to bot.

---

## 16. Follow-Up

- [ ] Follow-up tasks are created after lead/booking events.
- [ ] Max attempts are enforced.
- [ ] Stop rules are enforced.
- [ ] Follow-up is cancelled after paid/closed/handover.
- [ ] Follow-up messages use approved templates.

---

## 17. Queue/Worker

- [ ] Retry queue works.
- [ ] Dead-letter table receives failed tasks.
- [ ] Queue jobs are idempotent.
- [ ] Admin notification worker works.
- [ ] Summary worker works.
- [ ] Invoice expiry worker works.
- [ ] Follow-up worker works.

---

## 18. Testing

- [ ] Unit tests exist for idempotency.
- [ ] Unit tests exist for RAG retrieval.
- [ ] Unit tests exist for safety guard.
- [ ] Unit tests exist for booking calculation.
- [ ] Unit tests exist for invoice generation.
- [ ] Unit tests exist for payment evidence handover.
- [ ] Integration test exists for full chat-to-invoice flow.

---

## 19. Deployment

- [ ] Dockerfile exists.
- [ ] docker-compose.yml exists.
- [ ] PostgreSQL service included.
- [ ] Redis service included.
- [ ] Worker service included.
- [ ] Backup strategy documented.
- [ ] Log path documented.
- [ ] HTTPS/proxy expectation documented.

---

## Final Acceptance

- [ ] Starsender incoming chat creates contact, lead, conversation, and message.
- [ ] AI answers from knowledge base.
- [ ] AI refuses/fallbacks when data missing.
- [ ] Booking can produce invoice.
- [ ] Evidence locks bot and notifies admin.
- [ ] Admin can mark paid manually.
- [ ] System can later switch to WABA adapter without rewriting core services.
