"""Mailgun forwarder for operator mail.

Password reset, ban notices, and lift notices go through this forwarder.
Keycloak's mailer is not this path. Unconfigured Mailgun or an exhausted
10k monthly quota fails closed. The result never includes a password, a
reset link, or a Mailgun credential.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime

MONTHLY_QUOTA = 10_000
MESSAGE_NOT_SENT = "message was not sent"

_SECRET_MARKERS = ("key-", "api:", "mailgun", "password=", "reset_link")


@dataclass
class MailResult:
    sent: bool
    kind: str
    user_id: str
    reason: str = ""

    def public(self) -> dict[str, object]:
        """Browser payload. No link, password, or credential."""

        body = {
            "sent": self.sent,
            "kind": self.kind,
            "user_id": self.user_id,
            "detail": None if self.sent else MESSAGE_NOT_SENT,
        }
        encoded = str(body).lower()
        for marker in _SECRET_MARKERS:
            if marker in encoded:
                raise RuntimeError("operator mail result leaked a secret")
        return body


@dataclass
class MailQuota:
    limit: int = MONTHLY_QUOTA
    sent_by_month: dict[str, int] = field(default_factory=dict)

    def month_key(self, now: datetime | None = None) -> str:
        stamp = now or datetime.now(UTC)
        return stamp.strftime("%Y-%m")

    def used(self, now: datetime | None = None) -> int:
        return self.sent_by_month.get(self.month_key(now), 0)

    def exhausted(self, now: datetime | None = None) -> bool:
        return self.used(now) >= self.limit

    def mark_sent(self, now: datetime | None = None) -> None:
        key = self.month_key(now)
        self.sent_by_month[key] = self.used(now) + 1


@dataclass
class PreparedMessage:
    """Server-side only. Never attach this object to an HTTP response."""

    kind: str
    to_email: str
    subject: str
    text: str


class MailForwarder:
    def __init__(
        self,
        *,
        api_key: str | None,
        domain: str | None,
        sender: str | None,
        quota: MailQuota | None = None,
        transport=None,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.domain = (domain or "").strip()
        self.sender = (sender or "").strip()
        self.quota = quota or MailQuota()
        self.transport = transport
        self.posted: list[dict[str, str]] = []

    @classmethod
    def from_env(cls, *, quota: MailQuota | None = None, transport=None) -> MailForwarder:
        return cls(
            api_key=os.environ.get("MAILGUN_API_KEY"),
            domain=os.environ.get("MAILGUN_DOMAIN"),
            sender=os.environ.get("MAILGUN_FROM"),
            quota=quota,
            transport=transport,
        )

    def configured(self) -> bool:
        return bool(self.api_key and self.domain and self.sender)

    def send(self, message: PreparedMessage, *, user_id: str) -> MailResult:
        if not self.configured():
            return MailResult(
                sent=False,
                kind=message.kind,
                user_id=user_id,
                reason="mail_not_configured",
            )
        if self.quota.exhausted():
            return MailResult(
                sent=False,
                kind=message.kind,
                user_id=user_id,
                reason="quota_exhausted",
            )
        payload = {
            "from": self.sender,
            "to": message.to_email,
            "subject": message.subject,
            "text": message.text,
        }
        if self.transport is not None:
            self.transport(self.domain, self.api_key, payload)
        self.posted.append(
            {
                "kind": message.kind,
                "to": message.to_email,
                "domain": self.domain,
            }
        )
        self.quota.mark_sent()
        return MailResult(sent=True, kind=message.kind, user_id=user_id)


def prepare_password_reset(*, email: str, action_url: str) -> PreparedMessage:
    """Keycloak update-password is prepared here. Mailgun delivers it.

    `action_url` stays inside the message. Callers must not return it.
    """

    return PreparedMessage(
        kind="password_reset",
        to_email=email,
        subject="Reset your (s)panopticon password",
        text=(
            "A support operator requested a password reset. "
            f"Open this link to choose a new password: {action_url}"
        ),
    )


def prepare_ban_notice(*, email: str, lifted: bool) -> PreparedMessage:
    kind = "lift" if lifted else "ban"
    if lifted:
        text = "An operator lifted the ban on your access to this tenant."
        subject = "(s)panopticon access restored"
    else:
        text = "An operator banned your access to this tenant until the ban is lifted."
        subject = "(s)panopticon access banned"
    return PreparedMessage(kind=kind, to_email=email, subject=subject, text=text)


_QUOTA = MailQuota()


def mail_quota() -> MailQuota:
    return _QUOTA
