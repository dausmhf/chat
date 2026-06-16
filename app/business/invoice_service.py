import datetime
import os
import uuid
from pathlib import Path
from typing import Optional, Tuple
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.config import settings
from app.config import client_config_manager
from app.storage import models
from app.storage.repositories import InvoiceRepository
from fpdf import FPDF

class InvoicePDF(FPDF):
    def __init__(self, brand_name: str = "Daus Travel"):
        super().__init__()
        self.brand_name = brand_name

    def header(self):
        self.set_fill_color(20, 64, 92)
        self.rect(0, 0, 210, 30, style="F")
        self.set_text_color(255, 255, 255)
        self.set_font("Helvetica", "B", 16)
        self.cell(0, 8, self.brand_name, ln=True, align="L")
        self.set_font("Helvetica", "", 8)
        self.cell(0, 5, "Dummy Travel Umroh | Jl. Contoh Amanah No. 10, Jakarta", ln=True)
        self.cell(0, 5, "WA Admin: +62 896-9143-2547 | Izin PPIU: DUMMY-DAUS-TRAVEL", ln=True)
        self.set_text_color(0, 0, 0)
        self.ln(8)

    def footer(self):
        self.set_y(-15)
        self.set_font("Helvetica", "I", 8)
        self.cell(0, 10, "Disclaimer: Invoice ini bukan bukti lunas. Pembayaran sah setelah admin cek mutasi bank.", align="C")

def generate_invoice_number(db: Session, client_code: str) -> str:
    """
    Generates a unique invoice number format: INV-{CLIENT_CODE}-{YYYYMMDD}-{RUNNING_NUMBER}
    Resets daily per client.
    """
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    prefix = f"INV-{client_code.upper()}-{today_str}-"
    
    # Count invoices created today for this client
    count = db.query(models.Invoice).join(
        models.Client, models.Invoice.client_id == models.Client.id
    ).filter(
        models.Client.client_code == client_code,
        models.Invoice.invoice_number.like(f"{prefix}%")
    ).count()
    
    running_number = count + 1
    return f"{prefix}{running_number:04d}"

def create_booking_invoice(
    db: Session,
    client_uuid: uuid.UUID,
    client_code: str,
    booking_id: uuid.UUID
) -> Tuple[models.Invoice, str]:
    """
    Compiles data, increments revisions if existing, draws PDF layout, and records invoice state.
    """
    booking = db.query(models.Booking).filter(models.Booking.id == booking_id).first()
    if not booking:
        raise ValueError(f"Booking {booking_id} not found.")

    package = db.query(models.Package).filter(models.Package.id == booking.package_id).first()
    if not package:
        raise ValueError("Package not found on booking.")

    # Lookup active bank account
    bank_account = db.query(models.BankAccount).filter(
        models.BankAccount.client_id == client_uuid,
        models.BankAccount.is_active == True
    ).first()
    
    if not bank_account:
        payment_config = client_config_manager.load_json_config(client_code, "payment_config.json")
        active_accounts = [
            account for account in payment_config.get("bank_accounts", [])
            if account.get("is_active", True)
        ]
        if not active_accounts:
            raise ValueError(f"No active official bank account configured for client {client_code}.")
        configured = active_accounts[0]
        bank_account = models.BankAccount(
            client_id=client_uuid,
            bank_name=configured["bank_name"],
            account_number=configured["account_number"],
            account_holder=configured["account_holder"],
            version=_parse_bank_version(configured.get("version", 1)),
            is_active=True
        )
        db.add(bank_account)
        db.commit()
        db.refresh(bank_account)

    # Check if invoice already exists for this booking (for revisions)
    existing_invoice = db.query(models.Invoice).filter(
        models.Invoice.booking_id == booking_id
    ).order_by(models.Invoice.revision_number.desc()).first()

    if existing_invoice:
        revision_number = existing_invoice.revision_number + 1
        invoice_number = existing_invoice.invoice_number # Immutable number
    else:
        revision_number = 1
        invoice_number = generate_invoice_number(db, client_code)

    # Determine storage path
    base_dir = Path(__file__).parent.parent.parent.resolve()
    invoice_dir = base_dir / "clients" / client_code / "storage" / "invoices"
    invoice_dir.mkdir(parents=True, exist_ok=True)
    
    filename = f"{invoice_number}_rev{revision_number}.pdf"
    pdf_file_path = invoice_dir / filename

    # Draw PDF Layout using FPDF2
    try:
        pdf = InvoicePDF("Daus Travel")
        pdf.add_page()
        pdf.set_font("Helvetica", size=10)
        
        # Details
        pdf.set_font("Helvetica", "B", 14)
        pdf.cell(0, 9, "INVOICE PEMESANAN UMROH", ln=True, align="C")
        pdf.ln(2)
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 8, f"Invoice No: {invoice_number} (Rev {revision_number})", ln=True)
        pdf.cell(0, 8, f"Booking ID: {booking.id}", ln=True)
        pdf.cell(0, 8, f"Tanggal: {datetime.datetime.now().strftime('%Y-%m-%d')}", ln=True)
        pdf.cell(0, 8, f"Due Date: {(datetime.datetime.now() + datetime.timedelta(days=2)).strftime('%Y-%m-%d')}", ln=True)
        pdf.ln(5)
        
        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 8, "Detail Pemesan:", ln=True)
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 8, f"Nama: {booking.customer_name}", ln=True)
        pdf.cell(0, 8, f"Phone: {booking.customer_phone}", ln=True)
        pdf.cell(0, 8, f"Jumlah Pax: {booking.pax} orang", ln=True)
        pdf.ln(5)

        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 8, "Detail Paket:", ln=True)
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 8, f"Paket: {package.package_name} ({package.package_code})", ln=True)
        if package.duration_days:
            pdf.cell(0, 8, f"Durasi: {package.duration_days} hari", ln=True)
        pdf.cell(0, 8, f"Harga per Pax: IDR {package.price_per_pax:,}", ln=True)
        pdf.cell(0, 8, f"Subtotal: IDR {booking.total_amount:,}", ln=True)
        pdf.cell(0, 8, "Diskon: IDR 0", ln=True)
        pdf.cell(0, 8, f"Total Tagihan: IDR {booking.total_amount:,}", ln=True)
        pdf.ln(5)

        pdf.set_font("Helvetica", "B", 10)
        pdf.cell(0, 8, "Instruksi Pembayaran:", ln=True)
        pdf.set_font("Helvetica", size=10)
        pdf.cell(0, 8, f"Bank: {bank_account.bank_name}", ln=True)
        pdf.cell(0, 8, f"No Rekening: {bank_account.account_number}", ln=True)
        pdf.cell(0, 8, f"Atas Nama: {bank_account.account_holder}", ln=True)
        pdf.multi_cell(0, 6, "Catatan: transfer sesuai nominal invoice lalu kirim bukti transfer. Admin akan cek mutasi rekening resmi secara manual.")
        
        pdf.output(str(pdf_file_path))
        print(f"InvoiceService: PDF file generated successfully at {pdf_file_path}")
    except Exception as e:
        print(f"InvoiceService: PDF generation failed ({str(e)}), writing mock txt invoice file.")
        # Fallback to txt file if fpdf fails (e.g. environments without font engine)
        pdf_file_path = pdf_file_path.with_suffix(".txt")
        with open(pdf_file_path, "w", encoding="utf-8") as f:
            f.write(f"INVOICE {invoice_number} Rev {revision_number}\n")
            f.write(f"Customer: {booking.customer_name}\n")
            f.write(f"Amount: {booking.total_amount}\n")

    # Save to database
    due_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=2)
    invoice = models.Invoice(
        client_id=client_uuid,
        booking_id=booking.id,
        invoice_number=invoice_number,
        revision_number=revision_number,
        amount=booking.total_amount,
        pdf_path=str(pdf_file_path),
        payment_status="unpaid",
        due_at=due_at,
        bank_account_id=bank_account.id,
        bank_account_version=bank_account.version,
        package_price_version=package.price_version
    )
    db.add(invoice)
    
    # Update booking status to invoice_sent
    booking.status = "invoice_sent"
    
    db.commit()
    db.refresh(invoice)
    return invoice, str(pdf_file_path)


def _parse_bank_version(version_value) -> int:
    if isinstance(version_value, int):
        return version_value
    digits = "".join(ch for ch in str(version_value) if ch.isdigit())
    if not digits:
        return 1
    return int(digits[-6:])
