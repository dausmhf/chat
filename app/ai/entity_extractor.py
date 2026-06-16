import json
import re
from typing import Dict, Any, Optional
from app.ai.llm_client import LLMClient

class EntityExtractor:
    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client

    def extract_booking_entities(self, text: str) -> Dict[str, Any]:
        """
        Extracts booking parameters like name, phone, pax, and package details from user message.
        """
        if self.llm_client.api_key == "mock_key":
            # Return basic mock matching standard values
            pax_match = re.search(r"(\d+)\s*(pax|orang)", text.lower())
            pax = int(pax_match.group(1)) if pax_match else 1
            return {
                "customer_name": "Muhammad Firdaus",
                "customer_phone": "628123456789",
                "pax": pax,
                "package_code": "OCT12D"
            }

        prompt = """
        Extract the following fields from the user's message as a clean JSON object:
        - customer_name: Full name of the person booking (null if not mentioned)
        - customer_phone: Phone number of the user (null if not mentioned)
        - pax: Number of people traveling (as integer, default to null if not specified)
        - package_code: Package code or month traveling, e.g., 'OCT12D' or 'Oktober' (null if not mentioned)

        Output only the raw JSON object, without markdown blocks. Example format:
        {"customer_name": "Budi", "customer_phone": null, "pax": 2, "package_code": "OCT12D"}
        """
        try:
            response = self.llm_client.generate_chat_response(
                messages=[{"role": "user", "content": text}],
                system_prompt=prompt,
                temperature=0.0,
                max_tokens=150
            )
            # Clean possible markdown wrap ```json
            cleaned = response.replace("```json", "").replace("```", "").strip()
            data = json.loads(cleaned)
            return data
        except Exception as e:
            print(f"EntityExtractor error: {str(e)}")
            return {}
