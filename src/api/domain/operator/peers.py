"""Peer (s)panopticon rows on this plane's Postgres, plus a health ping.

v1 does not enroll a host, copy a device secret, or write a chain store.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Callable


@dataclass
class PeerRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    display_name: str
    base_url: str
    peer_tenant_id: str
    last_health_ok: bool
    last_health_detail: str
    checked_at: datetime

    def public(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "display_name": self.display_name,
            "base_url": self.base_url,
            "peer_tenant_id": self.peer_tenant_id,
            "last_health_ok": self.last_health_ok,
            "last_health_detail": self.last_health_detail,
            "checked_at": self.checked_at.isoformat(),
        }


@dataclass
class PeerBook:
    rows: list[PeerRecord] = field(default_factory=list)

    def clear(self) -> None:
        self.rows.clear()

    def for_tenant(self, tenant_id: uuid.UUID) -> list[PeerRecord]:
        return [row for row in self.rows if row.tenant_id == tenant_id]

    def save(
        self,
        *,
        tenant_id: uuid.UUID,
        display_name: str,
        base_url: str,
        peer_tenant_id: str,
        health_ok: bool,
        health_detail: str,
    ) -> PeerRecord:
        row = PeerRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            display_name=display_name.strip(),
            base_url=base_url.strip(),
            peer_tenant_id=peer_tenant_id.strip(),
            last_health_ok=health_ok,
            last_health_detail=health_detail[:500],
            checked_at=datetime.now(UTC),
        )
        self.rows.append(row)
        return row


_PEERS = PeerBook()


def peer_book() -> PeerBook:
    return _PEERS


def ping_health(base_url: str, *, fetch: Callable[[str], tuple[bool, str]] | None = None) -> tuple[bool, str]:
    """GET `{base}/health`. The fetch callable keeps tests off the network."""

    url = base_url.rstrip("/") + "/health"
    if fetch is None:
        return False, "health check not configured"
    try:
        ok, detail = fetch(url)
    except Exception as exc:  # noqa: BLE001 — health is down, not a crash
        return False, str(exc)[:200]
    return bool(ok), str(detail)[:200]
