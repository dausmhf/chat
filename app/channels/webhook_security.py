from typing import Mapping


def headers_with_query_secret(headers: Mapping[str, str], query_params: Mapping[str, str]) -> dict:
    normalized = dict(headers)
    query_secret = query_params.get("secret") or query_params.get("webhook_secret")
    if query_secret and "x-webhook-secret" not in normalized:
        normalized["x-webhook-secret"] = query_secret
    return normalized
