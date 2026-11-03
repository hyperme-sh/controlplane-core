"""Append-only operator audit. Never stores a password, link, or mail secret."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

_FORBIDDEN_DETAIL_KEYS = frozenset(
    {
        "password",
        "reset_link",
        "link",
        "mailgun_api_key",
        "api_key",
        "token",
        "enroll_token",
        "device_secret",
        "body",
    }
)


@dataclass
class OperatorAuditRow:
    id: uuid.UUID
    tenant_id: uuid.UUID
    actor: str
    action: str
    target_kind: str
    target_id: str
    reason: str
    created_at: datetime
    detail: dict[str, str] = field(default_factory=dict)

    def public(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "actor": self.actor,
            "action": self.action,
            "target_kind": self.target_kind,
            "target_id": self.target_id,
            "reason": self.reason,
            "created_at": self.created_at.isoformat(),
            "detail": dict(self.detail),
        }


class AuditLog:
    def __init__(self) -> None:
        self.rows: list[OperatorAuditRow] = []

    def clear(self) -> None:
        self.rows.clear()

    def append(
        self,
        *,
        tenant_id: uuid.UUID,
        actor: str,
        action: str,
        target_kind: str,
        target_id: str,
        reason: str,
        detail: dict[str, str] | None = None,
    ) -> OperatorAuditRow:
        safe: dict[str, str] = {}
        for key, value in (detail or {}).items():
            if key.lower() in _FORBIDDEN_DETAIL_KEYS:
                continue
            safe[key] = str(value)
        row = OperatorAuditRow(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            actor=actor,
            action=action,
            target_kind=target_kind,
            target_id=target_id,
            reason=reason.strip(),
            created_at=datetime.now(UTC),
            detail=safe,
        )
        self.rows.append(row)
        return row

    def for_tenant(self, tenant_id: uuid.UUID) -> list[OperatorAuditRow]:
        return [row for row in self.rows if row.tenant_id == tenant_id]


_LOG = AuditLog()


def operator_audit() -> AuditLog:
    return _LOG
