import datetime
import os
import uuid
from celery import Celery
from app.config import settings
from app.storage.database import SessionLocal
from app.storage import models
from app.channels.starsender_adapter import StarsenderAdapter

# Configure Celery with Redis broker/backend
celery_app = Celery(
    "umroh_tasks",
    broker=settings.redis_url,
    backend=settings.redis_url
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Jakarta",
    enable_utc=True,
    beat_schedule={
        "invoice-expiry-every-15-minutes": {
            "task": "app.workers.queue.task_invoice_expiry_check",
            "schedule": 900.0,
        },
        "followup-dispatch-every-5-minutes": {
            "task": "app.workers.queue.task_process_due_followups",
            "schedule": 300.0,
        },
        "admin-notification-dispatch-every-minute": {
            "task": "app.workers.queue.task_process_admin_notifications",
            "schedule": 60.0,
        },
    },
)

@celery_app.task(bind=True, max_retries=3)
def task_generate_invoice(self, booking_id_str: str, client_id_str: str, client_code: str):
    """
    Asynchronously generates invoice PDF.
    """
    db = SessionLocal()
    try:
        from app.business.invoice_service import create_booking_invoice
        client_uuid = uuid.UUID(client_id_str)
        booking_id = uuid.UUID(booking_id_str)
        
        # Create invoice
        invoice, pdf_path = create_booking_invoice(db, client_uuid, client_code, booking_id)
        print(f"Celery task_generate_invoice: Invoice created: {invoice.invoice_number} at {pdf_path}")
        return {"status": "success", "invoice_number": invoice.invoice_number}
    except Exception as exc:
        db.rollback()
        print(f"Celery task_generate_invoice error: {str(exc)}")
        # Log to dead letter if retries exhausted
        if self.request.retries >= self.max_retries:
            log_dead_letter(client_id_str, "invoice_queue", "generate_invoice", {"booking_id": booking_id_str}, str(exc))
        raise self.retry(exc=exc, countdown=10)
    finally:
        db.close()

@celery_app.task(bind=True, max_retries=3)
def task_generate_summary(self, conversation_id_str: str, client_id_str: str):
    """
    Asynchronously refreshes conversation summary.
    """
    db = SessionLocal()
    try:
        conv_id = uuid.UUID(conversation_id_str)
        client_uuid = uuid.UUID(client_id_str)
        
        # Load last N messages
        messages = db.query(models.Message).filter(
            models.Message.conversation_id == conv_id
        ).order_by(models.Message.created_at.desc()).limit(10).all()
        
        if not messages:
            return {"status": "no_messages"}

        # Format summary
        summary_text = f"Summary updated at {datetime.datetime.now().isoformat()}: "
        text_contents = [m.text_content for m in reversed(messages) if m.text_content]
        summary_text += " | ".join(text_contents)[:500]
        
        conversation = db.query(models.Conversation).filter(models.Conversation.id == conv_id).first()
        if conversation:
            conversation.summary = summary_text
            conversation.summary_updated_at = datetime.datetime.now(datetime.timezone.utc)
            db.commit()
            
        print(f"Celery task_generate_summary: Conversation {conversation_id_str} summary updated.")
        return {"status": "success"}
    except Exception as exc:
        db.rollback()
        if self.request.retries >= self.max_retries:
            log_dead_letter(client_id_str, "summary_queue", "generate_summary", {"conversation_id": conversation_id_str}, str(exc))
        raise self.retry(exc=exc, countdown=10)
    finally:
        db.close()

@celery_app.task
def task_invoice_expiry_check():
    """
    Periodic cron worker to scan unpaid invoices and transition status to expired.
    Checks due_at + grace window (default 2 hours).
    """
    db = SessionLocal()
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        grace_window = datetime.timedelta(hours=2)
        expiry_limit = now - grace_window
        
        # Find unpaid invoices past due limit
        expired_invoices = db.query(models.Invoice).filter(
            models.Invoice.payment_status == "unpaid",
            models.Invoice.due_at <= expiry_limit
        ).all()
        
        for inv in expired_invoices:
            inv.payment_status = "expired"
            
            # Transition booking status
            booking = db.query(models.Booking).filter(models.Booking.id == inv.booking_id).first()
            if booking:
                booking.status = "cancelled"
                
            # Log audit event
            audit = models.AuditLog(
                client_id=inv.client_id,
                actor_type="system",
                event_type="invoice_expired",
                entity_type="invoices",
                entity_id=inv.id,
                old_value={"status": "unpaid"},
                new_value={"status": "expired"}
            )
            db.add(audit)
            print(f"Celery expiry_check: Invoice {inv.invoice_number} is now expired.")
            
        db.commit()
        return {"status": "success", "expired_count": len(expired_invoices)}
    except Exception as e:
        db.rollback()
        print(f"Celery expiry_check failed: {str(e)}")
        return {"status": "failed", "error": str(e)}
    finally:
        db.close()

@celery_app.task
def task_process_admin_notifications():
    """
    Processes queued admin notifications. Delivery adapters can be plugged in here;
    for now the durable audit trail is created and the queue job is marked done.
    """
    db = SessionLocal()
    try:
        jobs = db.query(models.QueueJob).filter(
            models.QueueJob.queue_name == "notification_queue",
            models.QueueJob.status == "pending",
            models.QueueJob.next_run_at <= datetime.datetime.now(datetime.timezone.utc)
        ).order_by(models.QueueJob.created_at.asc()).limit(25).all()

        adapter = StarsenderAdapter()
        admin_phone = os.getenv("ADMIN_NOTIFICATION_PHONE", settings.admin_default_group_id)

        for job in jobs:
            delivery_text = _format_admin_notification(job.payload)
            send_result = adapter.send_text(admin_phone, delivery_text)
            if not send_result.success:
                job.attempt_count += 1
                job.last_error = send_result.error_message
                if job.attempt_count >= job.max_attempts:
                    job.status = "dead_letter"
                    db.add(models.DeadLetterJob(
                        original_job_id=job.id,
                        client_id=job.client_id,
                        queue_name=job.queue_name,
                        task_name=job.task_name,
                        payload=job.payload,
                        error_message=send_result.error_message,
                    ))
                else:
                    job.status = "pending"
                    job.next_run_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(minutes=2)
                continue

            job.status = "done"
            job.attempt_count += 1
            job.updated_at = datetime.datetime.now(datetime.timezone.utc)
            db.add(models.AuditLog(
                client_id=job.client_id,
                actor_type="system",
                event_type="admin_notification_dispatched",
                entity_type="queue_jobs",
                entity_id=job.id,
                new_value=job.payload,
            ))
            print(f"Admin notification delivered: {job.payload}")

        db.commit()
        return {"status": "success", "processed_count": len(jobs)}
    except Exception as e:
        db.rollback()
        print(f"Admin notification worker failed: {str(e)}")
        return {"status": "failed", "error": str(e)}
    finally:
        db.close()

@celery_app.task
def task_process_due_followups():
    """
    Marks due follow-up tasks for dispatch only if the conversation is still bot-safe.
    """
    db = SessionLocal()
    try:
        now = datetime.datetime.now(datetime.timezone.utc)
        tasks = db.query(models.FollowupTask).filter(
            models.FollowupTask.status == "pending",
            models.FollowupTask.scheduled_at <= now,
            models.FollowupTask.attempt_count < models.FollowupTask.max_attempts,
        ).order_by(models.FollowupTask.scheduled_at.asc()).limit(25).all()

        dispatched = 0
        stopped = 0
        for task in tasks:
            conversation = None
            if task.conversation_id:
                conversation = db.query(models.Conversation).filter(models.Conversation.id == task.conversation_id).first()
            if conversation and (not conversation.bot_enabled or conversation.status in {"handover_required", "human_active", "closed", "abuse_limited"}):
                task.status = "failed"
                task.stopped_reason = f"conversation_{conversation.status}"
                stopped += 1
                continue

            task.attempt_count += 1
            task.status = "done"
            task.updated_at = now
            dispatched += 1
            db.add(models.AuditLog(
                client_id=task.client_id,
                actor_type="system",
                event_type="followup_ready_for_delivery",
                entity_type="followup_tasks",
                entity_id=task.id,
                new_value={"template_key": task.template_key, "attempt_count": task.attempt_count},
            ))

        db.commit()
        return {"status": "success", "dispatched_count": dispatched, "stopped_count": stopped}
    except Exception as e:
        db.rollback()
        print(f"Follow-up worker failed: {str(e)}")
        return {"status": "failed", "error": str(e)}
    finally:
        db.close()


def _format_admin_notification(payload: dict) -> str:
    return (
        "URGENT HANDOVER REQUIRED\n"
        f"Reason: {payload.get('reason')}\n"
        f"Conversation ID: {payload.get('conversation_id')}\n"
        f"Nama: {payload.get('contact_name') or '-'}\n"
        f"Nomor WA: {payload.get('phone') or '-'}\n"
        f"Lead Stage: {payload.get('lead_stage') or '-'}\n"
        f"Invoice: {payload.get('invoice_number') or '-'}\n"
        f"Total: {payload.get('total_amount') or '-'}\n"
        "Ringkasan:\n"
        f"{payload.get('summary') or '-'}"
    )

def log_dead_letter(client_id_str: str, queue_name: str, task_name: str, payload: dict, error_message: str):
    """
    Logs failed background tasks to dead_letter_jobs table.
    """
    db = SessionLocal()
    try:
        dl_job = models.DeadLetterJob(
            client_id=uuid.UUID(client_id_str) if client_id_str else None,
            queue_name=queue_name,
            task_name=task_name,
            payload=payload,
            error_message=error_message
        )
        db.add(dl_job)
        db.commit()
    except Exception as e:
        print(f"Failed to record dead letter job: {str(e)}")
    finally:
        db.close()
