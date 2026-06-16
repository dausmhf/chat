from typing import Optional
from app.channels.starsender_adapter import StarsenderAdapter
from app.channels.waba_adapter import WabaCloudAdapter
from app.channels.base_adapter import MessageEvent

def normalize_payload(channel: str, payload: dict, client_id: str) -> Optional[MessageEvent]:
    """
    Normalizes payload using the appropriate channel adapter.
    """
    # Enforce injection of client_id if not present
    payload_copy = payload.copy()
    payload_copy["client_id"] = client_id
    
    if channel == "starsender":
        adapter = StarsenderAdapter()
        return adapter.parse_incoming(payload_copy)
    elif channel == "waba":
        adapter = WabaCloudAdapter()
        return adapter.parse_incoming(payload_copy)
    else:
        raise ValueError(f"Unsupported channel: {channel}")
