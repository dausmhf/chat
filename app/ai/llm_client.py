import os
import httpx
from typing import List, Dict, Any, Optional
from app.config import settings

class LLMClient:
    def __init__(self, client_id: str, ai_config: Dict[str, Any]):
        self.client_id = client_id
        self.ai_config = ai_config
        self.provider = ai_config.get("provider", "openai_compatible")
        self.base_url = ai_config.get("base_url", "https://api.openai.com/v1")
        
        # Load API key from env name referenced in config
        api_key_env_name = ai_config.get("api_key_env", "LLM_API_KEY")
        self.api_key = os.getenv(api_key_env_name, settings.llm_api_key)
        
        self.chat_model = ai_config.get("chat_model", "gpt-4o-mini")
        self.embedding_model = ai_config.get("embedding_model", "text-embedding-3-small")
        self.embedding_dimensions = int(ai_config.get("embedding_dimensions", 1536))
        self.thinking_budget = ai_config.get("thinking_budget")
        self.timeout = float(ai_config.get("timeout_seconds", 25))

    def _gemini_generation_config(self, temperature: float, max_tokens: int) -> Dict[str, Any]:
        generation_config: Dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if self.thinking_budget is not None:
            generation_config["thinkingConfig"] = {
                "thinkingBudget": int(self.thinking_budget),
            }
        return generation_config

    def validate_models(self) -> bool:
        """
        Runs a lightweight model check by sending a 1-token test prompt.
        If the provider rejects the model string, return False.
        """
        if self.api_key == "mock_key":
            print(f"LLMClient: Using mock key, skipping model validation for model '{self.chat_model}'.")
            return True

        if self.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.chat_model}:generateContent"
            params = {"key": self.api_key}
            data = {
                "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                "generationConfig": self._gemini_generation_config(0.0, 16),
            }
            try:
                response = httpx.post(url, params=params, json=data, timeout=5.0)
                if response.status_code == 200:
                    print(f"LLMClient: Gemini model '{self.chat_model}' validated successfully.")
                    return True
                print(f"LLMClient: Gemini model validation failed. Status: {response.status_code}, Response: {response.text}")
                return False
            except Exception as e:
                print(f"LLMClient: Exception checking Gemini model '{self.chat_model}': {str(e)}")
                return False
            
        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.chat_model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 1
        }
        try:
            response = httpx.post(url, headers=headers, json=data, timeout=5.0)
            if response.status_code == 200:
                print(f"LLMClient: Model '{self.chat_model}' validated successfully.")
                return True
            else:
                print(f"LLMClient: Model validation failed for '{self.chat_model}'. Status: {response.status_code}, Response: {response.text}")
                return False
        except Exception as e:
            print(f"LLMClient: Exception checking model '{self.chat_model}': {str(e)}")
            return False

    def generate_chat_response(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        temperature: float = 0.25,
        max_tokens: int = 650
    ) -> str:
        """
        Calls the LLM chat completion endpoint.
        """
        if self.api_key == "mock_key":
            # Return a generic mockup reply based on user content
            user_msg = messages[-1]["content"] if messages else ""
            if any(word in user_msg.lower() for word in ["harga", "biaya", "tarif"]):
                return "InsyaAllah saya bantu cekkan ya Ayah/Bunda. Untuk nominal paket, saya gunakan data resmi yang tersedia di sistem."
            if any(word in user_msg.lower() for word in ["admin", "cs", "manusia"]):
                return "Baik Ayah/Bunda, saya bantu teruskan ke admin agar dibantu langsung."
            return f"InsyaAllah saya bantu ya Ayah/Bunda. Pertanyaan yang saya terima: {user_msg}"

        if self.provider == "gemini":
            return self._generate_gemini_response(messages, system_prompt, temperature, max_tokens)

        url = f"{self.base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        # Build prompt payload with system prompt at top
        full_messages = [{"role": "system", "content": system_prompt}] + messages
        
        data = {
            "model": self.chat_model,
            "messages": full_messages,
            "temperature": temperature,
            "max_tokens": max_tokens
        }
        
        try:
            response = httpx.post(url, headers=headers, json=data, timeout=self.timeout)
            if response.status_code == 200:
                resp_json = response.json()
                return resp_json["choices"][0]["message"]["content"]
            else:
                raise RuntimeError(f"LLM API returned status {response.status_code}: {response.text}")
        except Exception as e:
            print(f"LLMClient generate_chat_response error: {str(e)}")
            raise e

    def _generate_gemini_response(
        self,
        messages: List[Dict[str, str]],
        system_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.chat_model}:generateContent"
        params = {"key": self.api_key}
        contents = []
        for msg in messages:
            role = "model" if msg.get("role") == "assistant" else "user"
            contents.append({"role": role, "parts": [{"text": msg.get("content", "")}]})

        data = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": contents,
            "generationConfig": self._gemini_generation_config(temperature, max_tokens),
        }

        response = httpx.post(url, params=params, json=data, timeout=self.timeout)
        if response.status_code != 200:
            raise RuntimeError(f"Gemini API returned status {response.status_code}: {response.text}")

        payload = response.json()
        candidates = payload.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini API returned no candidates.")
        parts = candidates[0].get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts).strip()

    def embed_text(self, text: str) -> List[float]:
        """
        Returns an embedding vector for RAG search. Mock mode returns a stable 1536-dim vector.
        """
        if self.api_key == "mock_key":
            seed = sum(ord(ch) for ch in text[:200]) or 1
            return [((seed + i) % 997) / 997 for i in range(1536)]

        if self.provider == "gemini":
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.embedding_model}:embedContent"
            params = {"key": self.api_key}
            data = {
                "content": {"parts": [{"text": text}]},
                "outputDimensionality": self.embedding_dimensions,
            }
            response = httpx.post(url, params=params, json=data, timeout=self.timeout)
            if response.status_code != 200:
                raise RuntimeError(f"Gemini embedding API returned status {response.status_code}: {response.text}")
            return response.json()["embedding"]["values"]

        url = f"{self.base_url.rstrip('/')}/embeddings"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {"model": self.embedding_model, "input": text}
        response = httpx.post(url, headers=headers, json=data, timeout=self.timeout)
        if response.status_code != 200:
            raise RuntimeError(f"Embedding API returned status {response.status_code}: {response.text}")
        return response.json()["data"][0]["embedding"]
            
    def get_sliding_window_messages(
        self,
        chat_history: List[Dict[str, str]],
        max_turns: int = 5
    ) -> List[Dict[str, str]]:
        """
        Implements sliding window memory rule. Keeps only the last N turns.
        Each turn is usually a user message + an assistant reply.
        """
        # 1 turn = 2 messages (user + assistant).
        # Max turns * 2 = number of messages.
        max_msg_count = max_turns * 2
        return chat_history[-max_msg_count:]
