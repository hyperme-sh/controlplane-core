"""HTTP client for api-keys-service internal validation.

Shared so the gateway and the hypermesh router run the same check. The
service rejects a key whose tenant does not match the requested one, so a
truthy ``valid`` already implies the tenant bound to that key.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx

from api.core.config import settings


async def validate_api_key_remote(
    *,
    base_url: str,
    api_key: str,
    tenant_id: uuid.UUID,
    timeout_s: float = 5.0,
) -> dict[str, Any] | None:
    """Return validation JSON dict or None on failure."""
    url = base_url.rstrip("/") + "/api/v1/internal/validate"
    headers = {"X-Internal-Token": settings.api_keys_internal_token}
    payload = {"api_key": api_key, "tenant_id": str(tenant_id)}
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError:
        return None
    if r.status_code != 200:
        return None
    data = r.json()
    if not data.get("valid"):
        return None
    return data
