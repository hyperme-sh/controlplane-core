"""A platform issuer subject can legitimately have tenant-local projections."""

import uuid
from unittest.mock import Mock

import pytest
from api.common import base_repository
from api.infrastructure.persistence.repositories.user_repository_impl import (
    UserRepository,
)
from api.usecases.user_service import UserService
from controlplane_db.models import Base, Role, Tenant, User
from sqlalchemy import create_engine, select
from sqlalchemy.exc import MultipleResultsFound
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
import os
from sqlalchemy.engine import make_url
from sqlalchemy import text


@pytest.fixture(scope="module")
def database():
    base = make_url(os.environ['TEST_DATABASE_URL']).set(drivername='postgresql+psycopg')
    name = 'core_test_' + uuid.uuid4().hex
    admin = create_engine(base, isolation_level='AUTOCOMMIT')
    engine = None
    try:
        with admin.connect() as cx:
            cx.execute(text(f'CREATE DATABASE "{name}"'))
        target = base.set(database=name)
        engine = create_engine(target)
        Base.metadata.create_all(engine, tables=[Tenant.__table__, Role.__table__, User.__table__])
        yield target.set(drivername='postgresql+asyncpg').render_as_string(hide_password=False)
    finally:
        if engine is not None:
            engine.dispose()
        with admin.connect() as cx:
            cx.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.mark.asyncio
async def test_platform_login_does_not_resolve_foreign_tenant_projection(
    database, monkeypatch
):
    engine = create_async_engine(database)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            tenant = Tenant(name="foreign-" + uuid.uuid4().hex)
            session.add(tenant)
            await session.flush()
            subject = str(uuid.uuid4())
            # Insert the tenant projection first so insertion order cannot select it.
            common = {
                "first_name": "Test",
                "last_name": "Owner",
                "gender": "prefer_not_to_say",
                "is_active": True,
                "keycloak_sub": subject,
            }
            foreign = User(
                **common, tenant_id=tenant.id, email="tenant@example.com"
            )
            platform = User(**common, tenant_id=None, email="platform@example.com")
            session.add_all([foreign, platform])
            await session.flush()
            monkeypatch.setattr(base_repository, "get_db_session", lambda: session)
            repository = UserRepository()
            # Reproduce the pre-fix failure against real PostgreSQL uniqueness scopes.
            with pytest.raises(MultipleResultsFound):
                await repository.single_or_none(keycloak_sub=subject)
            service = UserService(repository, Mock(), Mock())
            claims = {"sub": subject, "email": platform.email, "email_verified": True}
            resolved = await service.ensure_user_from_keycloak_claims(claims)
            assert resolved.id == platform.id
            assert resolved.tenant_id is None
            assert resolved.role_id is None
            assert (
                foreign.tenant_id == tenant.id
                and foreign.email == "tenant@example.com"
            )
            assert not session.dirty and not session.new
            assert (
                len(
                    (
                        await session.scalars(
                            select(User).where(User.keycloak_sub == subject)
                        )
                    ).all()
                )
                == 2
            )
            await session.rollback()
    finally:
        await engine.dispose()
