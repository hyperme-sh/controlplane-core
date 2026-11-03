from typing import Literal

from controlplane_db.models import BillingRecord, StripeSettings

PaymentType = Literal["one_time", "recurring", "both"]
ScopeType = Literal["host", "tenant"]
StatusType = Literal[
    "paid",
    "pending",
    "requires_payment_method",
    "requires_action",
    "active",
    "succeeded",
    "payment_failed",
    "canceled",
    "incomplete",
]
ActorType = Literal["tenant", "end_user"]

__all__ = [
    "StripeSettings",
    "BillingRecord",
    "PaymentType",
    "ScopeType",
    "StatusType",
    "ActorType",
]
