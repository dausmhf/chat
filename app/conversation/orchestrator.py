import uuid
from typing import Any, Dict, List, Optional, Tuple
from sqlalchemy.orm import Session

from app.admin.notification_service import create_admin_notification, create_handover
from app.ai.entity_extractor import EntityExtractor
from app.ai.intent_detector import IntentDetector
from app.ai.llm_client import LLMClient
from app.ai.prompt_builder import build_system_prompt
from app.ai.safety_guard import validate_response
from app.business.booking_service import calculate_and_create_booking
from app.business.invoice_service import create_booking_invoice
from app.channels.factory import build_channel_adapter
from app.config import client_config_manager
from app.conversation.state_manager import can_bot_reply
from app.ingestion.event_handler import get_or_create_client_uuid, handle_incoming_webhook
from app.ingestion.rate_limiter import check_rate_limit
from app.payment.manual_payment_service import triage_incoming_file
from app.rag.cache_manager import get_cached_answer, store_cached_answer
from app.rag.retriever import RAGRetriever, RAGRetrievalResult
from app.rag.source_trace_logger import log_source_trace
from app.storage import models
from app.storage.repositories import MessageRepository, PackageRepository


def process_incoming_message(
    db: Session,
    channel: str,
    payload: dict,
    headers: Optional[dict] = None,
    send_reply: bool = True,
) -> Dict[str, Any]:
    """
    Production conversation flow:
    capture -> idempotency -> rate limit -> intent/RAG/business action -> safety -> send/log reply.
    """
    headers = headers or {}
    client_code = payload.get("client_id", "travel_alfalah")
    configs = client_config_manager.load_all_configs(client_code)
    client_uuid = get_or_create_client_uuid(db, client_code)

    ingest_result = handle_incoming_webhook(db, channel, payload, headers)
    if ingest_result.get("status") != "success":
        return {"status": ingest_result.get("status"), "ingestion": ingest_result}

    message = _get_message(db, ingest_result["message_id"], client_uuid)
    conversation = _get_conversation(db, ingest_result["conversation_id"], client_uuid)
    contact = db.query(models.Contact).filter(
        models.Contact.id == conversation.contact_id,
        models.Contact.client_id == client_uuid,
    ).first()
    lead_profile = db.query(models.LeadProfile).filter(
        models.LeadProfile.contact_id == conversation.contact_id,
        models.LeadProfile.client_id == client_uuid,
    ).first()

    adapter = _get_adapter(channel, configs["channel"])

    file_size = _payload_file_size(payload)
    if file_size and file_size > 10 * 1024 * 1024:
        reply = "Mohon maaf Ayah/Bunda, file yang dikirim terlalu besar. Silakan kirim ulang dengan ukuran maksimal 10 MB atau minta admin membantu."
        _send_and_log(db, client_uuid, conversation, contact, adapter, reply, send_reply)
        create_admin_notification(db, client_uuid, conversation, "large_file_upload", "File lebih dari 10 MB ditolak.", contact, lead_profile)
        return {"status": "limited", "reason": "large_file_upload", "reply": reply}

    rate = check_rate_limit(db, client_uuid, conversation.contact_id, conversation, message.text_content or "")
    if not rate.allowed:
        _send_and_log(db, client_uuid, conversation, contact, adapter, rate.user_message, send_reply)
        create_admin_notification(db, client_uuid, conversation, rate.reason, rate.user_message, contact, lead_profile)
        return {"status": "limited", "reason": rate.reason, "reply": rate.user_message}

    if message.message_type in {"image", "file"}:
        is_evidence, evidence_reply = triage_incoming_file(db, client_uuid, conversation, message)
        if is_evidence:
            _send_and_log(db, client_uuid, conversation, contact, adapter, evidence_reply, send_reply)
            create_admin_notification(db, client_uuid, conversation, "payment_evidence_received", evidence_reply, contact, lead_profile)
            return {"status": "handover", "reason": "payment_evidence_received", "reply": evidence_reply}
        if evidence_reply:
            _send_and_log(db, client_uuid, conversation, contact, adapter, evidence_reply, send_reply)
            return {"status": "replied", "intent": "file_received", "reply": evidence_reply}

    if not can_bot_reply(conversation):
        return {"status": "stored_only", "reason": f"bot_paused:{conversation.status}"}

    llm_client = LLMClient(client_code, configs["ai"])
    intent_detector = IntentDetector(llm_client)
    intent, confidence = intent_detector.detect_intent(message.text_content or "")
    _record_intent(db, client_uuid, conversation, message, intent, confidence)
    _update_lead(db, lead_profile, intent)

    min_confidence = float(configs["ai"].get("safety", {}).get("intent_confidence_min", 0.70))
    if confidence < min_confidence:
        reply = "Untuk memastikan jawabannya akurat, saya bantu teruskan ke admin ya Ayah/Bunda."
        create_handover(db, client_uuid, conversation, "low_confidence", reply, contact, lead_profile)
        _send_and_log(db, client_uuid, conversation, contact, adapter, reply, send_reply)
        return {"status": "handover", "intent": intent, "confidence": confidence, "reply": reply}

    if intent in {"human_request", "complaint"}:
        reply = "Baik Ayah/Bunda, saya bantu teruskan ke admin agar dibantu langsung."
        create_handover(db, client_uuid, conversation, intent, reply, contact, lead_profile)
        _send_and_log(db, client_uuid, conversation, contact, adapter, reply, send_reply)
        return {"status": "handover", "intent": intent, "reply": reply}

    if intent == "payment_evidence":
        reply = (
            "Jika Ayah/Bunda sudah transfer, silakan kirim bukti pembayaran berupa gambar atau file. "
            "Nanti admin akan cek mutasi rekening resmi secara manual."
        )
        _send_and_log(db, client_uuid, conversation, contact, adapter, reply, send_reply)
        return {"status": "replied", "intent": intent, "reply": reply}

    if intent == "booking_intent":
        return _handle_booking(db, client_uuid, client_code, conversation, contact, lead_profile, message, adapter, llm_client, configs, send_reply)

    if intent in {"ask_package", "ask_price", "unknown", "acknowledgement"}:
        reply, rag_handover, rag_result, cache_hit = _answer_with_rag_or_package_data(
            db, client_uuid, client_code, contact, conversation, message, llm_client, configs
        )
        is_safe, final_reply = validate_response(
            reply,
            configs["payment"],
            context_available=not rag_handover,
            intent=intent,
        )
        if rag_handover or not is_safe:
            create_handover(db, client_uuid, conversation, "knowledge_or_safety_fallback", final_reply, contact, lead_profile)
        outgoing = _send_and_log(db, client_uuid, conversation, contact, adapter, final_reply, send_reply)
        if rag_result:
            log_source_trace(
                db=db,
                client_id=client_uuid,
                conversation_id=conversation.id,
                message_id=outgoing.id,
                normalized_query=rag_result.normalized_query,
                confidence=rag_result.confidence,
                knowledge_version=rag_result.knowledge_version,
                sources=rag_result.sources,
                metadata={
                    "cache_hit": cache_hit,
                    "requires_handover": rag_handover,
                    "handover_reason": rag_result.handover_reason,
                    "is_safe": is_safe,
                },
            )
            if _should_cache_rag_answer(configs, rag_result, rag_handover, is_safe, cache_hit):
                token_cfg = configs["ai"].get("token_optimization", {})
                store_cached_answer(
                    db=db,
                    client_id=client_uuid,
                    normalized_query=rag_result.normalized_query,
                    knowledge_version=rag_result.knowledge_version,
                    answer_text=final_reply,
                    confidence=rag_result.confidence,
                    sources=rag_result.sources,
                    ttl_minutes=int(token_cfg.get("response_cache_ttl_minutes", 60)),
                )
        return {"status": "handover" if rag_handover else "replied", "intent": intent, "reply": final_reply}

    reply = "InsyaAllah saya bantu ya Ayah/Bunda. Untuk memastikan jawabannya tepat, saya teruskan ke admin."
    create_handover(db, client_uuid, conversation, "unhandled_intent", reply, contact, lead_profile)
    _send_and_log(db, client_uuid, conversation, contact, adapter, reply, send_reply)
    return {"status": "handover", "intent": intent, "reply": reply}


def _handle_booking(
    db: Session,
    client_uuid: uuid.UUID,
    client_code: str,
    conversation: models.Conversation,
    contact: models.Contact,
    lead_profile: Optional[models.LeadProfile],
    message: models.Message,
    adapter: Any,
    llm_client: LLMClient,
    configs: Dict[str, Any],
    send_reply: bool,
) -> Dict[str, Any]:
    entities = EntityExtractor(llm_client).extract_booking_entities(message.text_content or "")
    missing = [field for field in ["customer_name", "customer_phone", "pax", "package_code"] if not entities.get(field)]
    if missing:
        reply = "Boleh dibantu lengkapi nama pemesan, nomor WhatsApp, pilihan paket/jadwal, dan jumlah pax ya Ayah/Bunda?"
        _send_and_log(db, client_uuid, conversation, contact, adapter, reply, send_reply)
        return {"status": "collecting_booking", "missing": missing, "reply": reply}

    booking, requires_handover, booking_reply = calculate_and_create_booking(
        db=db,
        client_uuid=client_uuid,
        contact_id=contact.id,
        conversation_id=conversation.id,
        package_code=str(entities["package_code"]),
        customer_name=str(entities["customer_name"]),
        customer_phone=str(entities["customer_phone"]),
        pax=int(entities["pax"]),
    )

    if requires_handover or not booking:
        create_handover(db, client_uuid, conversation, "booking_requires_admin", booking_reply, contact, lead_profile)
        _send_and_log(db, client_uuid, conversation, contact, adapter, booking_reply, send_reply)
        return {"status": "handover", "intent": "booking_intent", "reply": booking_reply}

    invoice = None
    invoice_path = None
    if configs["feature_flags"].get("invoice_generation", True):
        invoice, invoice_path = create_booking_invoice(db, client_uuid, client_code, booking.id)

    reply = booking_reply
    if invoice:
        reply += (
            f"\n\nInvoice {invoice.invoice_number} sudah dibuat dengan total IDR {invoice.amount:,}. "
            "Invoice ini bukan bukti lunas; pembayaran tetap dicek manual oleh admin."
        )
    _send_and_log(db, client_uuid, conversation, contact, adapter, reply, send_reply)

    if invoice_path and send_reply:
        send_result = adapter.send_file(contact.phone_e164, invoice_path, "Invoice pemesanan umroh")
        MessageRepository(db, client_uuid).log_message(
            conversation_id=conversation.id,
            direction="outgoing",
            message_type="file",
            file_path=invoice_path,
            provider_message_id=send_result.message_id,
            contact_id=contact.id,
            sender_type="bot",
            metadata={"send_success": send_result.success, "error": send_result.error_message},
        )

    return {"status": "replied", "intent": "booking_intent", "booking_id": str(booking.id), "invoice_id": str(invoice.id) if invoice else None, "reply": reply}


def _answer_with_rag_or_package_data(
    db: Session,
    client_uuid: uuid.UUID,
    client_code: str,
    contact: models.Contact,
    conversation: models.Conversation,
    message: models.Message,
    llm_client: LLMClient,
    configs: Dict[str, Any],
) -> Tuple[str, bool, Optional[RAGRetrievalResult], bool]:
    text = message.text_content or ""
    retrieved_chunks: List[str] = []
    requires_handover = False
    rag_result: Optional[RAGRetrievalResult] = None
    cache_hit = False

    if configs["feature_flags"].get("rag_answering", True):
        try:
            query_embedding = llm_client.embed_text(text)
            threshold = float(configs["ai"].get("safety", {}).get("retrieval_score_min", 0.55))
            token_cfg = configs["ai"].get("token_optimization", {})
            top_k = int(token_cfg.get("rag_top_k", 3))
            rag_result = RAGRetriever(db, client_uuid).retrieve(
                query_text=text,
                query_embedding=query_embedding,
                min_threshold=threshold,
                top_k=top_k,
                conversation_id=conversation.id,
            )
            requires_handover = rag_result.requires_handover or rag_result.confidence < threshold
            if token_cfg.get("response_cache_enabled", True) and rag_result.chunks and not requires_handover:
                cached = get_cached_answer(db, client_uuid, rag_result.normalized_query, rag_result.knowledge_version)
                if cached:
                    cache_hit = True
                    rag_result.confidence = float(cached.confidence)
                    rag_result.sources = list(cached.sources or [])
                    return cached.answer_text, False, rag_result, cache_hit
            max_chunk_tokens = int(token_cfg.get("max_chunk_tokens", 500))
            retrieved_chunks = [_trim_chunk(item.chunk.chunk_text, max_chunk_tokens) for item in rag_result.chunks]
        except Exception as exc:
            print(f"ConversationOrchestrator: RAG retrieval failed: {str(exc)}")

    if _requires_explicit_rag_context(text) and not retrieved_chunks:
        return "Untuk detail itu saya bantu cekkan ke admin ya Ayah/Bunda, agar jawabannya sesuai itinerary terbaru dari travel.", True, rag_result, cache_hit

    package_context = _package_context(db, client_uuid)
    if package_context:
        retrieved_chunks.append(package_context)
        if not (rag_result and rag_result.chunks):
            requires_handover = False

    if not retrieved_chunks:
        return "Untuk data itu saya bantu teruskan ke admin ya Ayah/Bunda, agar jawabannya lebih pasti sesuai data terbaru travel.", True, rag_result, cache_hit

    history = _recent_history(db, conversation.id, max_messages=8)
    system_prompt = build_system_prompt(
        client_id=client_code,
        contact_name=contact.display_name or "Ayah/Bunda",
        conversation_summary=conversation.summary,
        retrieved_chunks=retrieved_chunks[:5],
    )
    response = llm_client.generate_chat_response(
        messages=llm_client.get_sliding_window_messages(history + [{"role": "user", "content": text}], max_turns=5),
        system_prompt=system_prompt,
        temperature=float(configs["ai"].get("temperature", 0.25)),
        max_tokens=int(configs["ai"].get("max_output_tokens", 650)),
    )
    if rag_result and 0.55 <= rag_result.confidence < 0.78 and not requires_handover:
        response = response.rstrip() + "\n\nKalau Ayah/Bunda ingin kepastian terakhir, saya bisa bantu teruskan ke admin."
    return response, requires_handover, rag_result, cache_hit


def _requires_explicit_rag_context(text: str) -> bool:
    lowered = text.lower()
    operational_keywords = (
        "makkah dulu",
        "mekkah dulu",
        "madinah dulu",
        "hotel",
        "maskapai",
        "pesawat",
        "tanggal berangkat",
        "jadwal berangkat",
        "seat",
        "sisa kursi",
        "fasilitas",
        "boleh nggak",
        "boleh gak",
        "bisa nggak",
        "bisa gak",
    )
    return any(keyword in lowered for keyword in operational_keywords)


def _package_context(db: Session, client_uuid: uuid.UUID) -> str:
    packages = PackageRepository(db, client_uuid).get_packages()
    if not packages:
        return ""
    lines = ["Data paket aktif dari database:"]
    for package in packages[:8]:
        seat = f", sisa seat {package.remaining_seat}" if package.remaining_seat is not None else ""
        duration = f", durasi {package.duration_days} hari" if package.duration_days else ""
        lines.append(
            f"- {package.package_name} ({package.package_code}): IDR {package.price_per_pax:,} per pax{duration}{seat}, status {package.status}."
        )
    return "\n".join(lines)


def _recent_history(db: Session, conversation_id: uuid.UUID, max_messages: int) -> List[Dict[str, str]]:
    messages = db.query(models.Message).filter(
        models.Message.conversation_id == conversation_id,
        models.Message.text_content.isnot(None),
    ).order_by(models.Message.created_at.desc()).limit(max_messages).all()
    history = []
    for msg in reversed(messages):
        role = "assistant" if msg.direction == "outgoing" else "user"
        history.append({"role": role, "content": msg.text_content or ""})
    return history


def _record_intent(db: Session, client_uuid: uuid.UUID, conversation: models.Conversation, message: models.Message, intent: str, confidence: float) -> None:
    db.add(models.IntentResult(
        client_id=client_uuid,
        conversation_id=conversation.id,
        message_id=message.id,
        intent=intent,
        confidence=confidence,
        entities={},
        missing_fields=[],
        model_name="rule_or_llm",
    ))
    db.commit()


def _update_lead(db: Session, lead_profile: Optional[models.LeadProfile], intent: str) -> None:
    if not lead_profile:
        return
    lead_profile.last_intent = intent
    if intent == "booking_intent":
        lead_profile.stage = "booking_intent"
    elif intent in {"ask_price", "ask_package"} and lead_profile.stage == "new_lead":
        lead_profile.stage = "interested_package"
    tags = list(lead_profile.tags or [])
    if intent not in tags:
        tags.append(intent)
    lead_profile.tags = tags
    db.commit()


def _send_and_log(
    db: Session,
    client_uuid: uuid.UUID,
    conversation: models.Conversation,
    contact: models.Contact,
    adapter: Any,
    reply: str,
    send_reply: bool,
) -> models.Message:
    provider_message_id = ""
    send_success = False
    error_message = ""
    if send_reply:
        result = adapter.send_text(contact.phone_e164, reply)
        provider_message_id = result.message_id
        send_success = result.success
        error_message = result.error_message
    return MessageRepository(db, client_uuid).log_message(
        conversation_id=conversation.id,
        direction="outgoing",
        message_type="text",
        text_content=reply,
        provider_message_id=provider_message_id,
        contact_id=contact.id,
        sender_type="bot",
        metadata={"send_success": send_success, "error": error_message},
    )


def _trim_chunk(text: str, max_tokens: int) -> str:
    words = (text or "").split()
    if len(words) <= max_tokens:
        return text
    return " ".join(words[:max_tokens]) + " ..."


def _should_cache_rag_answer(
    configs: Dict[str, Any],
    rag_result: RAGRetrievalResult,
    rag_handover: bool,
    is_safe: bool,
    cache_hit: bool,
) -> bool:
    token_cfg = configs["ai"].get("token_optimization", {})
    if cache_hit or not token_cfg.get("response_cache_enabled", True):
        return False
    if rag_handover or not is_safe:
        return False
    return bool(rag_result.sources and rag_result.confidence >= 0.78)


def _get_adapter(channel: str, channel_config: Dict[str, Any]) -> Any:
    return build_channel_adapter(channel, channel_config)


def _get_message(db: Session, message_id: str, client_uuid: uuid.UUID) -> models.Message:
    message = db.query(models.Message).filter(
        models.Message.id == uuid.UUID(message_id),
        models.Message.client_id == client_uuid,
    ).first()
    if not message:
        raise ValueError(f"Message {message_id} not found.")
    return message


def _get_conversation(db: Session, conversation_id: str, client_uuid: uuid.UUID) -> models.Conversation:
    conversation = db.query(models.Conversation).filter(
        models.Conversation.id == uuid.UUID(conversation_id),
        models.Conversation.client_id == client_uuid,
    ).first()
    if not conversation:
        raise ValueError(f"Conversation {conversation_id} not found.")
    return conversation


def _payload_file_size(payload: dict) -> Optional[int]:
    for key in ("file_size", "fileSize", "size", "media_size"):
        value = payload.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            return None
    return None
