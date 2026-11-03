"""Durable operator bans.

Ban writes one active `operator_bans` row. It does not soft-delete, pause
sell, or hold scheduling. Suspend uses only the temporary verbs that already
exist and never writes this table. There is no user Suspend verb.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock

SUBJECT_KINDS = frozenset({"user", "group", "device"})

# Temporary verbs. None of these insert a ban row.
SUSPEND_VERBS = {
    "device": frozenset({"sell_pause", "schedule_hold"}),
    "contract": frozenset({"suspend", "stop", "refund"}),
}

# Shown, not a switch. A later green cert clears it.
CERT_SUSPENDED = "suspended"

HOST_POSTURE_ACTIONS = frozenset(
    {
        "sell_pause",
        "schedule_hold",
        "schedule_restore",
        "deenroll",
        "enroll_token",
    }
)
HOST_IMAGE_ACTIONS = frozenset({"image_push", "firmware_push", "update_host"})


class BanError(ValueError):
    pass


class NoUserSuspend(BanError):
    pass


@dataclass
class BanRecord:
    id: uuid.UUID
    tenant_id: uuid.UUID
    subject_kind: str
    subject_id: uuid.UUID
    reason: str
    created_by: str
    created_at: datetime
    lifted_at: datetime | None = None
    lifted_by: str | None = None
    lift_reason: str | None = None
    subject_label: str | None = None

    @property
    def active(self) -> bool:
        return self.lifted_at is None

    def public(self) -> dict[str, object]:
        return {
            "id": str(self.id),
            "tenant_id": str(self.tenant_id),
            "subject_kind": self.subject_kind,
            "subject_id": str(self.subject_id),
            "reason": self.reason,
            "created_by": self.created_by,
            "created_at": self.created_at.isoformat(),
            "lifted_at": self.lifted_at.isoformat() if self.lifted_at else None,
            "lifted_by": self.lifted_by,
            "lift_reason": self.lift_reason,
            "subject_label": self.subject_label,
            "active": self.active,
        }


@dataclass
class BanBook:
    """In-process ban index. Postgres is the durable copy on the user-service."""

    rows: list[BanRecord] = field(default_factory=list)
    suspend_log: list[dict[str, str]] = field(default_factory=list)
    side_effects: list[str] = field(default_factory=list)
    _lock: Lock = field(default_factory=Lock)

    def clear(self) -> None:
        with self._lock:
            self.rows.clear()
            self.suspend_log.clear()
            self.side_effects.clear()

    def active_row(
        self,
        tenant_id: uuid.UUID,
        subject_kind: str,
        subject_id: uuid.UUID,
    ) -> BanRecord | None:
        for row in self.rows:
            if (
                row.tenant_id == tenant_id
                and row.subject_kind == subject_kind
                and row.subject_id == subject_id
                and row.active
            ):
                return row
        return None

    def ban(
        self,
        *,
        tenant_id: uuid.UUID,
        subject_kind: str,
        subject_id: uuid.UUID,
        reason: str,
        created_by: str,
        subject_label: str | None = None,
    ) -> BanRecord:
        kind = subject_kind.strip().lower()
        if kind not in SUBJECT_KINDS:
            raise BanError("subject_kind must be user, group, or device")
        why = reason.strip()
        if not why:
            raise BanError("reason is required")
        if self.active_row(tenant_id, kind, subject_id) is not None:
            raise BanError("subject already has an active ban")
        row = BanRecord(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            subject_kind=kind,
            subject_id=subject_id,
            reason=why,
            created_by=created_by,
            created_at=datetime.now(UTC),
            subject_label=subject_label,
        )
        with self._lock:
            self.rows.append(row)
        return row

    def lift(
        self,
        *,
        tenant_id: uuid.UUID,
        ban_id: uuid.UUID,
        lift_reason: str,
        lifted_by: str,
    ) -> BanRecord:
        why = lift_reason.strip()
        if not why:
            raise BanError("lift reason is required")
        for row in self.rows:
            if row.id == ban_id and row.tenant_id == tenant_id and row.active:
                row.lifted_at = datetime.now(UTC)
                row.lifted_by = lifted_by
                row.lift_reason = why
                return row
        raise BanError("active ban not found")

    def suspend(
        self,
        *,
        subject_kind: str,
        verb: str,
        subject_id: str,
    ) -> dict[str, str]:
        """Record a temporary verb. Does not insert a ban row."""

        kind = subject_kind.strip().lower()
        if kind == "user":
            raise NoUserSuspend("v0 has no temporary user suspend")
        allowed = SUSPEND_VERBS.get(kind)
        if not allowed or verb not in allowed:
            raise BanError(f"{verb} is not a suspend verb for {kind}")
        entry = {
            "subject_kind": kind,
            "subject_id": subject_id,
            "verb": verb,
        }
        with self._lock:
            self.suspend_log.append(entry)
        return entry

    def user_may_enter(self, tenant_id: uuid.UUID, user_id: uuid.UUID) -> bool:
        return self.active_row(tenant_id, "user", user_id) is None

    def group_grant_allowed(self, tenant_id: uuid.UUID, group_id: uuid.UUID) -> bool:
        return self.active_row(tenant_id, "group", group_id) is None

    def device_may_enroll(self, tenant_id: uuid.UUID, device_id: uuid.UUID) -> bool:
        return self.active_row(tenant_id, "device", device_id) is None

    def device_may_take_lease(self, tenant_id: uuid.UUID, device_id: uuid.UUID) -> bool:
        return self.active_row(tenant_id, "device", device_id) is None

    def device_may_schedule(self, tenant_id: uuid.UUID, device_id: uuid.UUID) -> bool:
        return self.active_row(tenant_id, "device", device_id) is None


_BOOK = BanBook()


def ban_book() -> BanBook:
    return _BOOK


def host_action_allowed(action: str) -> bool:
    """Posture verbs only. Image and firmware rollout stay on the box."""

    return action in HOST_POSTURE_ACTIONS and action not in HOST_IMAGE_ACTIONS
