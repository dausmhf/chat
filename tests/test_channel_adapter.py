from app.channels.starsender_adapter import StarsenderAdapter

def test_starsender_parse_incoming():
    adapter = StarsenderAdapter()
    payload = {
        "messageId": "msg_test_123",
        "message": "Saya mau tanya paket Oktober",
        "phone": "628123456789",
        "name": "Muhammad Firdaus",
        "client_id": "travel_alfalah"
    }
    
    event = adapter.parse_incoming(payload)
    assert event.event_id == "msg_test_123"
    assert event.sender_phone == "628123456789"
    assert event.sender_name == "Muhammad Firdaus"
    assert event.text == "Saya mau tanya paket Oktober"
    assert event.idempotency_key == "starsender:msg_test_123"
    assert event.channel == "starsender"

def test_starsender_parse_incoming_generates_id_without_provider_message_id():
    adapter = StarsenderAdapter()
    first = adapter.parse_incoming({
        "message": "Halo",
        "phone": "628123456789",
        "name": "Muhammad Firdaus",
        "client_id": "travel_alfalah"
    })
    second = adapter.parse_incoming({
        "message": "Saya mau tanya paket lain",
        "phone": "628123456789",
        "name": "Muhammad Firdaus",
        "client_id": "travel_alfalah"
    })

    assert first.event_id.startswith("generated_")
    assert first.event_id != "unknown_id"
    assert first.idempotency_key != second.idempotency_key

def test_starsender_send_text():
    adapter = StarsenderAdapter(api_key="mock_key")
    result = adapter.send_text("628123456789", "Halo Ayah/Bunda")
    assert result.success is True
    assert result.message_id == "mock_starsender_msg_id"

def test_starsender_health_check():
    adapter = StarsenderAdapter(api_key="mock_key")
    health = adapter.health_check()
    assert health.status == "healthy"
    assert health.can_send_message is True
