"""
Invoice Due Date Reminder Worker
Run as cron job every 1-6 hours to check for unpaid invoices nearing due date.
Sends reminders at 24h and 6h before due date, and marks overdue after due date passes.

Usage:
    python -m app.workers.invoice_reminder
"""

import datetime
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from sqlalchemy.orm import Session
from sqlalchemy import func
from app.config import settings, client_config_manager
from app.storage.database import SessionLocal
from app.storage import models
from app.channels.factory import build_channel_adapter


REMINDER_WINDOWS = [
    (24, "24 jam"),
    (6, "6 jam"),
]

OVERDUE_HOURS = 1  # mark overdue 1 hour after due date


def _get_reminder_stage(invoice: models.Invoice, now: datetime.datetime) -> str:
    """Determine reminder stage for an unpaid invoice."""
    due_at = invoice.due_at
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=datetime.timezone.utc)

    remaining_hours = (due_at - now).total_seconds() / 3600

    if remaining_hours <= -OVERDUE_HOURS:
        return "overdue"
    if remaining_hours <= 6:
        return "6h"
    if remaining_hours <= 24:
        return "24h"
    return None  # not yet in reminder window


def _build_reminder_message(
    invoice: models.Invoice,
    contact_name: str,
    stage: str,
    remaining_hours: float,
) -> str:
    """Build a natural reminder message."""
    if stage == "24h":
        return (
            f"Assalamualaikum {contact_name},\n\n"
            f"Hanya mengingatkan, invoice {invoice.invoice_number} sebesar "
            f"IDR {invoice.amount:,} akan jatuh tempo besok. "
            f"Mohon segera selesaikan pembayaran agar jadwal keberangkatan "
            f"tetap aman ya.\n\n"
            f"Setelah transfer, silakan kirim buktinya di sini untuk dicek admin. "
            f"Terima kasih."
        )
    elif stage == "6h":
        return (
            f"Assalamualaikum {contact_name},\n\n"
            f"Pengingat terakhir: invoice {invoice.invoice_number} sebesar "
            f"IDR {invoice.amount:,} akan jatuh tempo dalam {int(remaining_hours)} jam. "
            f"Mohon segera ditransfer ya, agar booking tidak hangus.\n\n"
            f"Jika sudah transfer, kirim buktinya di sini."
        )
    else:  # overdue
        return (
            f"Mohon maaf {contact_name}, invoice {invoice.invoice_number} telah "
            f"melewati batas waktu pembayaran. Booking Anda akan ditahan sementara.\n\n"
            f"Silakan hubungi admin untuk informasi lebih lanjut. "
            f"Terima kasih."
        )


def _already_reminded(db: Session, invoice_id, stage: str) -> bool:
    """Check if a reminder at this stage was already sent."""
    key = f"invoice-remind:{invoice_id}:{stage}"
    existing = db.query(models.QueueJob).filter(
        models.QueueJob.idempotency_key == key,
        models.QueueJob.status.in_(["pending", "processing", "done"]),
    ).first()
    return existing is not None


def run_reminders():
    """Main worker: check all unpaid invoices and queue reminders."""
    db: Session = SessionLocal()
    now = datetime.datetime.now(datetime.timezone.utc)

    try:
        # Find unpaid invoices
        invoices = (
            db.query(models.Invoice)
            .filter(
                models.Invoice.payment_status == "unpaid",
            )
            .order_by(models.Invoice.due_at.asc())
            .all()
        )

        sent_count = 0
        overdue_count = 0

        for invoice in invoices:
            stage = _get_reminder_stage(invoice, now)
            if not stage:
                continue

            # Prevent duplicate reminders
            if _already_reminded(db, invoice.id, stage):
                continue

            # Resolve contact
            booking = db.query(models.Booking).filter(
                models.Booking.id == invoice.booking_id
            ).first()
            if not booking:
                continue

            contact = db.query(models.Contact).filter(
                models.Contact.id == booking.contact_id
            ).first()
            if not contact:
                continue

            due_at = invoice.due_at
            if due_at.tzinfo is None:
                due_at = due_at.replace(tzinfo=datetime.timezone.utc)
            remaining_hours = (due_at - now).total_seconds() / 3600
            contact_name = contact.display_name or "Ayah/Bunda"

            message = _build_reminder_message(invoice, contact_name, stage, remaining_hours)

            # Queue the notification job
            db.add(models.QueueJob(
                client_id=invoice.client_id,
                queue_name="notification_queue",
                task_name="send_invoice_reminder",
                idempotency_key=f"invoice-remind:{invoice.id}:{stage}",
                payload={
                    "phone": contact.phone_e164,
                    "contact_name": contact_name,
                    "invoice_id": str(invoice.id),
                    "invoice_number": invoice.invoice_number,
                    "stage": stage,
                    "message": message,
                },
                status="pending",
                max_attempts=3,
            ))

            # If overdue, mark the booking
            if stage == "overdue":
                invoice.payment_status = "admin_overdue"
                if booking:
                    booking.status = "cancelled"
                    booking.updated_at = now
                overdue_count += 1

            sent_count += 1
            print(f"[Reminder] {stage} for invoice {invoice.invoice_number} to {contact.phone_e164}")

        if sent_count > 0 or overdue_count > 0:
            db.commit()
            print(f"[Reminder] Done. {sent_count} reminders queued, {overdue_count} overdue marked.")
        else:
            print("[Reminder] No reminders needed.")

    except Exception as exc:
        db.rollback()
        print(f"[Reminder] Error: {exc}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    run_reminders()
