from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from api.common.utils import get_logger
from api.domain.dtos.billing_dto import BillingRecordDto, BillingRecordListDto
from api.infrastructure.persistence.db_session import get_db_session

from controlplane_db.models import BillingRecord

logger = get_logger(__name__)


def _parse_uuid(v: Any) -> uuid.UUID | None:
    if v is None:
        return None
    if isinstance(v, uuid.UUID):
        return v
    return uuid.UUID(str(v))


def _parse_dt(v: Any) -> datetime | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)):
        return datetime.fromtimestamp(int(v), tz=timezone.utc)
    return None


class BillingRecordRepository:
    @property
    def session(self):
        return get_db_session()

    def _row_to_dto(self, doc: BillingRecord) -> BillingRecordDto:
        def ts(dt: datetime | None) -> int | None:
            if dt is None:
                return None
            return int(dt.timestamp())

        return BillingRecordDto(
            scope=doc.scope,  # type: ignore[arg-type]
            actor=doc.actor,  # type: ignore[arg-type]
            user_id=str(doc.user_id) if doc.user_id else None,
            payment_type=doc.payment_type,
            currency=doc.currency,
            amount=doc.amount,
            stripe_customer_id=doc.stripe_customer_id,
            stripe_subscription_id=doc.stripe_subscription_id,
            stripe_session_id=doc.stripe_session_id,
            product_id=doc.product_id,
            price_id=doc.price_id,
            status=doc.status,  # type: ignore[arg-type]
            current_period_end=ts(doc.current_period_end),
            canceled_at=ts(doc.canceled_at),
            cancellation_reason=doc.cancellation_reason,
            metadata=dict(doc.extra_metadata or {}),
        )

    async def create(self, data: dict[str, Any]) -> BillingRecord:
        meta = data.get("metadata") or data.get("extra_metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        br = BillingRecord(
            scope=data["scope"],
            actor=data["actor"],
            tenant_id=_parse_uuid(data.get("tenant_id")),
            user_id=_parse_uuid(data.get("user_id")),
            payment_type=data["payment_type"],
            currency=data["currency"],
            amount=data.get("amount"),
            stripe_customer_id=data.get("stripe_customer_id"),
            stripe_subscription_id=data.get("stripe_subscription_id"),
            stripe_session_id=data.get("stripe_session_id"),
            product_id=data.get("product_id"),
            price_id=data.get("price_id"),
            status=data.get("status") or "pending",
            current_period_end=_parse_dt(data.get("current_period_end")),
            canceled_at=_parse_dt(data.get("canceled_at")),
            cancellation_reason=data.get("cancellation_reason"),
            extra_metadata=meta,
        )
        self.session.add(br)
        await self.session.flush()
        return br

    async def single_or_none(self, session_id: str) -> BillingRecord | None:
        res = await self.session.execute(
            select(BillingRecord).where(BillingRecord.stripe_session_id == session_id)
        )
        return res.scalar_one_or_none()

    async def search(self, filters: dict[str, Any]) -> list[BillingRecord]:
        q = select(BillingRecord)
        if tid := filters.get("tenant_id"):
            q = q.where(BillingRecord.tenant_id == _parse_uuid(tid))
        if uid := filters.get("user_id"):
            q = q.where(BillingRecord.user_id == _parse_uuid(uid))
        if st := filters.get("status"):
            q = q.where(BillingRecord.status == st)
        res = await self.session.execute(q)
        return list(res.scalars().all())

    async def update(self, record_id: str | uuid.UUID, data: dict[str, Any]) -> None:
        rid = _parse_uuid(record_id)
        rec = await self.session.get(BillingRecord, rid)
        if rec is None:
            return
        for k, v in data.items():
            if k in ("id",):
                continue
            if k == "metadata":
                rec.extra_metadata = v if isinstance(v, dict) else {}
            elif k == "current_period_end":
                rec.current_period_end = _parse_dt(v)
            elif k == "canceled_at":
                rec.canceled_at = _parse_dt(v)
            elif hasattr(rec, k):
                setattr(rec, k, v)
        await self.session.flush()

    async def list_paginated(self, skip: int = 0, limit: int = 10) -> BillingRecordListDto:
        logger.debug("Listing billing records skip=%s limit=%s", skip, limit)
        cnt = await self.session.execute(select(func.count()).select_from(BillingRecord))
        total = int(cnt.scalar_one())
        res = await self.session.execute(select(BillingRecord).offset(skip).limit(limit))
        docs = list(res.scalars().all())
        return BillingRecordListDto(
            billing_records=[self._row_to_dto(doc) for doc in docs],
            skip=skip,
            limit=limit,
            total=total,
            has_previous=skip > 0,
            has_next=skip + limit < total,
        )
