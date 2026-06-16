# Autonomous AI CS Engine for Travel Umroh

A production-ready, multi-tenant AI Customer Service and booking engine designed specifically for Travel Umroh agencies. It features WhatsApp ingestion (via Starsender or WABA), automated booking draft creation, pgvector-based RAG knowledge extraction, safety filters, immutable PDF invoice generation, and background workers for followup reminders and invoice expiations.

---

## 1. Core Principles & Design

1. **Manual Payment Verification Only:** AI or background workers will never mark invoices/payments as `paid`. This status must only be set by authenticated operators/admins.
2. **Multi-Tenant Isolation:** Every tenant-owned database table is isolated by `client_id`, and queries filter explicitly to prevent cross-tenant leakage.
3. **Modular Channel Adapter System:** Adapters process payloads into normalized `MessageEvent` schemas, decoupling Starsender and WABA credentials from backend logic.
4. **Idempotency Safeguards:** Checks and increments duplicate webhook payload counters on `webhook_idempotency_keys` before trigger processing.

---

## 2. Directory Structure

```text
app/
  main.py                # FastAPI app entry point
  config.py              # Configuration loader for global and client settings
  bootstrap.py           # Startup checks & validations
  storage/
    database.py          # Session factory & SQL engines
    models.py            # SQLAlchemy models with SQLite/Postgres types compatibility
    repositories.py      # Scoped CRUD handlers
    migrate.py           # SQL database migrations runner
  channels/
    base_adapter.py      # Abstract base ChannelAdapter interface
    starsender_adapter.py# Starsender WhatsApp implementation
    waba_adapter.py      # WABA Cloud API adapter
  ingestion/
    idempotency.py       # Payload deduplication helper
    contact_resolver.py  # Maps sender phone number to Client contacts
    event_handler.py     # Main webhook event logic
  ai/
    llm_client.py        # LLM completions wrapper
    prompt_builder.py    # Memory-aware prompt builder
    intent_detector.py   # Intent & Entity classifiers
    entity_extractor.py  # Extracts package, passenger, and count entities
    safety_guard.py      # Content policies filter
  business/
    booking_service.py   # Passenger rules, price calculation, draft builder
    invoice_service.py   # Immutable PDF compiler using fpdf2
  payment/
    manual_payment_service.py # Evidence triage, bot lock status transition
  admin/
    admin_command_service.py  # Override command overrides (/bot_on, unlock)
  workers/
    queue.py             # Celery background tasks & dead letter logging
clients/
  travel_alfalah/        # Sample configurations & assets folder
    config/              # Client JSON configurations
    knowledge/           # Client markdown documents
    invoices/            # Generated immutable invoices
    uploads/             # User-uploaded payment evidence files
tests/                   # Complete test suite
```

---

## 3. Setup & Installation

### Option A: Local Run (No Docker)

1. **Python version:** Use Python 3.12 for local runtime parity with Docker. Python 3.14 is not recommended with the pinned FastAPI/Pydantic stack.
2. **Create virtual env & install dependencies:**
   ```bash
   python -m venv venv
   source venv/Scripts/activate  # On Windows
   pip install -r requirements.txt
   ```
3. **Configure Environment:**
   Copy `.env.example` to `.env` and configure credentials:
   ```bash
   cp .env.example .env
   ```
4. **Apply Migrations:**
   Runs the migrations dynamically by executing the SQL statements inside `DATABASE_SCHEMA.sql`:
   ```bash
   python -m app.storage.migrate
   ```
5. **Run the FastAPI App:**
   ```bash
   uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
   ```
6. **Run Celery Worker:**
   ```bash
   celery -A app.workers.queue.celery_app worker --loglevel=info
   ```
7. **Run Unit Tests:**
   ```bash
   python -m pytest tests/
   ```

### Option B: Docker Deployment (PostgreSQL + Redis + App + Celery)

1. **Build & Start Services:**
   ```bash
   docker-compose up --build
   ```
   This will automatically spin up:
   - **Database (`db`):** `ankane/pgvector` on port `5432`.
   - **Redis (`redis`):** `redis:7-alpine` on port `6379`.
   - **Application (`app`):** FastAPI app on port `8000`.
   - **Celery Worker (`celery_worker`):** Celery background tasks.
   - **Celery Beat (`celery_beat`):** periodic invoice expiry, follow-up, and admin notification dispatch.

### Key Runtime Environment

For Gemini-first deployment, set:

```bash
GEMINI_API_KEY=...
STARSENDER_API_KEY=...
STARSENDER_WEBHOOK_SECRET=...
```

Webhook targets:

```text
POST /webhooks/starsender/travel_alfalah
POST /webhooks/waba/travel_alfalah
```

Admin operation endpoints:

```text
POST /admin/{client_code}/takeover
POST /admin/{client_code}/bot-on
POST /admin/{client_code}/mark-payment
```

---

## 4. Production Expectation

### HTTPS & Reverse Proxy
- **Expectation:** Do not expose FastAPI (`app` container) directly to the web. Always place a reverse proxy like **Nginx** or **Caddy** in front of it to handle TLS termination (HTTPS).
- **Configuration Example (Nginx):**
  ```nginx
  server {
      listen 443 ssl;
      server_name api.travelumroh.com;

      ssl_certificate /etc/letsencrypt/live/api.travelumroh.com/fullchain.pem;
      ssl_certificate_key /etc/letsencrypt/live/api.travelumroh.com/privkey.pem;

      location / {
          proxy_pass http://localhost:8000;
          proxy_set_header Host $host;
          proxy_set_header X-Real-IP $remote_addr;
          proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
          proxy_set_header X-Forwarded-Proto $scheme;
      }
  }
  ```

### Log Path & Retention
- **Application Logs:** In container environments, log stdout/stderr to standard streams.
- **Docker Log Driver:** Configure log rotation in your host's `/etc/docker/daemon.json` to prevent disk depletion:
  ```json
  {
    "log-driver": "json-file",
    "log-opts": {
      "max-size": "100m",
      "max-file": "3"
    }
  }
  ```

### Backup Strategy
1. **PostgreSQL Database:** Scheduled cron job to backup postgres data using `pg_dump`:
   ```bash
   pg_dump -h localhost -U postgres -d aics -F c -b -v -f /backups/aics_$(date +\%F).backup
   ```
2. **Uploaded Assets & Invoices:** Ensure directory `/app/storage` (defined as shared volume `shared-storage` in `docker-compose.yml`) is backed up daily to an offsite S3-compatible object store.
3. **Redis:** RDB snapshots enabled in redis configuration.

---

## 5. Known Limitations & TODOs

- **Admin notification delivery:** Notifications are now queued and audited, but the final delivery adapter to Telegram/WhatsApp admin group must be wired to the agency's preferred channel.
- **WABA file sending:** Text sending is implemented for Meta Cloud API when credentials exist. File sending still needs a public media URL/upload workflow before enabling live invoice delivery through WABA.
- **Invoice visual polish:** PDF invoices are immutable and functional, but logo, address, permit number, and richer package breakdown should be added before client-facing production rollout.
