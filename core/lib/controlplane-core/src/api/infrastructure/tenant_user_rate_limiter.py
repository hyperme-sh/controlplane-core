"""In-process sliding-window rate limit per tenant + user (shared by ai-service, acl-service, …)."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque


class TenantUserRateLimiter:
    def __init__(self, *, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: dict[tuple[uuid.UUID, str], deque[float]] = defaultdict(deque)

    def allow(self, tenant_id: uuid.UUID, user_key: str) -> bool:
        now = time.monotonic()
        window = 60.0
        key = (tenant_id, user_key)
        dq = self._hits[key]
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= self._per_minute:
            return False
        dq.append(now)
        return True
