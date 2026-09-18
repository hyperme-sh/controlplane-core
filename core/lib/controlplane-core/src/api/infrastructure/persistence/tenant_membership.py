"""Internal tenant-membership writer.

A membership is a ``users`` row with ``tenant_id`` set, matched later by
email or ``keycloak_sub``. This is the same insert the invite repository
uses. It is a service/repository call with an explicit tenant id.

Not a public self-join. Not ``POST /api/v1/users`` (that HTTP door still
requires ``can_invite_members``, names, and a tenant claim on the JWT).
A raw OIDC renter cannot call that route. Not ``POST /api/v1/tenants/``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from controlplane_db.models import User


async def get_tenant_membership_row(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    email: str | None = None,
    keycloak_sub: str | None = None,
) -> User | None:
    if email:
        found = await session.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                func.lower(User.email) == email.strip().lower(),
                User.deleted_at.is_(None),
            )
        )
        row = found.scalar_one_or_none()
        if row is not None:
            return row
    if keycloak_sub:
        found = await session.execute(
            select(User).where(
                User.tenant_id == tenant_id,
                User.keycloak_sub == keycloak_sub,
                User.deleted_at.is_(None),
            )
        )
        return found.scalar_one_or_none()
    return None


async def _keycloak_sub_is_free(session: AsyncSession, keycloak_sub: str) -> bool:
    found = await session.execute(
        select(User.id).where(User.keycloak_sub == keycloak_sub)
    )
    return found.scalar_one_or_none() is None


async def create_tenant_membership_row(
    session: AsyncSession,
    tenant_id: uuid.UUID,
    *,
    email: str,
    first_name: str,
    last_name: str,
    gender: str = "unspecified",
    is_active: bool = True,
    keycloak_sub: str | None = None,
) -> User:
    """Insert the membership row. Caller is the service, not a renter JWT."""
    sub = keycloak_sub.strip() if keycloak_sub and keycloak_sub.strip() else None
    if sub is not None and not await _keycloak_sub_is_free(session, sub):
        sub = None
    row = User(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        email=str(email).strip().lower(),
        first_name=first_name,
        last_name=last_name,
        gender=gender,
        is_active=is_active,
        activated_at=datetime.now(timezone.utc) if is_active else None,
        keycloak_sub=sub,
    )
    session.add(row)
    await session.flush()
    await session.refresh(row)
    return row
