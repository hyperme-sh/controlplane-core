"""In-process sliding-window rate limit per tenant + user (shared by ai-service, acl-service, …)."""

from __future__ import annotations

import time
import uuid
from collections import defaultdict, deque


class TenantUserRateLimiter:
    def __init__(self, *, per_minute: int) -> None:
        self._per_minute = per_minute
        self._hits: dict[tuple[uuid.UUID, str], deque[float]] = defaultdict(deque)
        self._swept = time.monotonic()

    def allow(self, tenant_id: uuid.UUID, user_key: str) -> bool:
        now = time.monotonic()
        window = 60.0
        if now - self._swept > window:
            # Drop idle keys so caller-chosen keys cannot grow memory forever.
            for idle in [k for k, hits in self._hits.items() if not hits or now - hits[-1] > window]:
                del self._hits[idle]
            self._swept = now
        key = (tenant_id, user_key)
        dq = self._hits[key]
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= self._per_minute:
            return False
        dq.append(now)
        return True
