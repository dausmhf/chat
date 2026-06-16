from app.ai.llm_client import LLMClient


class DummyResponse:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.text = str(payload)

    def json(self):
        return self._payload


def test_gemini_chat_sends_thinking_budget(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None, headers=None):
        captured["json"] = json
        return DummyResponse({
            "candidates": [{
                "content": {"parts": [{"text": "OK"}]},
                "finishReason": "STOP",
            }]
        })

    monkeypatch.setenv("GEMINI_API_KEY", "test_key")
    monkeypatch.setattr("app.ai.llm_client.httpx.post", fake_post)

    client = LLMClient("travel_alfalah", {
        "provider": "gemini",
        "api_key_env": "GEMINI_API_KEY",
        "chat_model": "gemini-2.5-flash",
        "thinking_budget": 0,
    })
    response = client.generate_chat_response(
        messages=[{"role": "user", "content": "ping"}],
        system_prompt="reply briefly",
        max_tokens=64,
    )

    assert response == "OK"
    assert captured["json"]["generationConfig"]["thinkingConfig"]["thinkingBudget"] == 0


def test_gemini_embedding_keeps_configured_dimensions(monkeypatch):
    captured = {}

    def fake_post(url, params=None, json=None, timeout=None, headers=None):
        captured["json"] = json
        return DummyResponse({"embedding": {"values": [0.1, 0.2]}})

    monkeypatch.setenv("GEMINI_API_KEY", "test_key")
    monkeypatch.setattr("app.ai.llm_client.httpx.post", fake_post)

    client = LLMClient("travel_alfalah", {
        "provider": "gemini",
        "api_key_env": "GEMINI_API_KEY",
        "embedding_model": "gemini-embedding-001",
        "embedding_dimensions": 1536,
    })
    embedding = client.embed_text("cek rag")

    assert embedding == [0.1, 0.2]
    assert captured["json"]["outputDimensionality"] == 1536
