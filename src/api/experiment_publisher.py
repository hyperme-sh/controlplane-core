"""Dedicated private publisher process; never receives HTTP signing requests."""
import asyncio
import os
from pathlib import Path
from uuid import UUID

import anyio
import httpx
from cryptography.hazmat.primitives.serialization import load_pem_private_key
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from controlplane_api.config import settings
from controlplane_db.session import normalize_database_url
from api.domain.experiments.publication import Publisher
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects
from api.infrastructure.externals.tenant_record_azure import AzureTenantRecords
from api.infrastructure.externals.tenant_record_s3 import S3TenantRecords
from api.infrastructure.persistence.db_session import set_db_session, reset_db_session
from api.infrastructure.persistence.tenant_guc import set_current_tenant_id_guc
from api.interfaces.api_controllers.tenant_objects_endpoint import tenant_backend


def signing_binding():
    # Provision a separate key for each tenant/audience. Consumers pin its public
    # key; the private file belongs only to this dedicated publisher process.
    tenant = UUID(os.environ["EXPERIMENT_TENANT_ID"])
    audience = os.environ["EXPERIMENT_AUDIENCE"]
    with Path(os.environ["EXPERIMENT_SIGNING_KEY_FILE"]).open("rb") as handle:
        raw = handle.read(4097)
    if len(raw) > 4096:
        raise ValueError("invalid signing key file")
    key = load_pem_private_key(raw, password=None)
    # Constructor validates key type and binding before opening any connection.
    Publisher(tenant, audience, key, None)
    return tenant, audience, key


async def tick(maker, tenant, audience, key, now, *, source=None, verify=True):
    async with maker() as session:
        token = set_db_session(session)
        try:
            await set_current_tenant_id_guc(session, tenant)
            async with tenant_backend(tenant) as backend:
                if isinstance(backend, AzureTenantObjects):
                    records = AzureTenantRecords(backend.container)
                elif isinstance(backend, S3TenantObjects):
                    records = S3TenantRecords(backend)
                else:
                    raise ValueError("private experiment provider required")
                publisher = Publisher(tenant, audience, key, records)
                if source is None:
                    changed = await publisher.publish(now)
                else:
                    from api.domain.experiments.growthbook import import_and_publish
                    async with httpx.AsyncClient(trust_env=False, verify=verify) as client:
                        changed = await import_and_publish(source, client, publisher, now)
            await session.commit()
            return changed
        except BaseException:
            await session.rollback()
            raise
        finally:
            reset_db_session(token)


async def run(*, source=None, verify=True):
    import time
    tenant, audience, key = signing_binding()
    if source is not None and (source.tenant != tenant or source.audience != audience):
        raise ValueError('GrowthBook source must match the signing binding')
    engine = create_async_engine(normalize_database_url(settings.database_url))
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        while True:
            try:
                with anyio.fail_after(30):
                    await tick(maker, tenant, audience, key, int(time.time()), source=source, verify=verify)
            except Exception:
                # Provider/configuration exceptions can contain private values.
                print("Experiment publication unavailable", flush=True)
            await asyncio.sleep(30)
    finally:
        await engine.dispose()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit("Experiment publisher configuration unavailable") from None


if __name__ == "__main__":
    main()
