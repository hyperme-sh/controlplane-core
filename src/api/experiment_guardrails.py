"""Private guardrail controller; no browser endpoint or signing key."""
import asyncio
import os
import stat
from uuid import UUID

import anyio
import httpx
from azure.identity.aio import ClientSecretCredential
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from controlplane_api.config import settings
from controlplane_db.session import normalize_database_url
from api.domain.experiments.control import disable_regressed
from api.domain.experiments.metrics import AzureGuardrailMetrics
from api.domain.experiments.store import ExperimentStore
from api.experiment_retention import listing
from api.infrastructure.persistence.db_session import set_db_session, reset_db_session
from api.infrastructure.persistence.tenant_guc import set_current_tenant_id_guc
from api.interfaces.api_controllers.tenant_objects_endpoint import tenant_backend


def private_secret(path):
    if not os.path.isabs(path):
        raise ValueError("Absolute private credential path required")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1 or not 16 <= info.st_size <= 4096:
            raise ValueError("Private credential file required")
        value = os.read(descriptor, 4097).decode().strip()
        if not 16 <= len(value) <= 4096 or any(ord(c) < 32 for c in value):
            raise ValueError("Invalid private credential")
        return value
    finally:
        os.close(descriptor)


async def tick(maker, tenant, audience, metrics):
    async with maker() as session:
        context = set_db_session(session)
        try:
            await set_current_tenant_id_guc(session, tenant)
            async with tenant_backend(tenant) as objects:
                records, _page = listing(objects)
                disabled = await disable_regressed(ExperimentStore(tenant, records), metrics, tenant=tenant, audience=audience)
            await session.commit()
            return disabled
        except BaseException:
            await session.rollback()
            raise
        finally:
            reset_db_session(context)


async def run():
    tenant = UUID(os.environ["EXPERIMENT_TENANT_ID"])
    audience = os.environ["EXPERIMENT_AUDIENCE"]
    workspace = UUID(os.environ["EXPERIMENT_METRICS_WORKSPACE"])
    directory, client_id = UUID(os.environ["EXPERIMENT_METRICS_DIRECTORY"]), UUID(os.environ["EXPERIMENT_METRICS_CLIENT_ID"])
    if not directory.int or not client_id.int:
        raise ValueError("Explicit credential binding required")
    secret = private_secret(os.environ["EXPERIMENT_METRICS_SECRET_FILE"])
    # No developer, CLI, browser or unrelated managed-identity fallback.
    async with ClientSecretCredential(str(directory), str(client_id), secret) as credential, httpx.AsyncClient(trust_env=False) as client:
        metrics = AzureGuardrailMetrics(workspace, tenant, audience, credential, client)
        engine = create_async_engine(normalize_database_url(settings.database_url))
        maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        try:
            while True:
                try:
                    with anyio.fail_after(30):
                        disabled = await tick(maker, tenant, audience, metrics)
                    print(f"Experiment guardrails evaluated; disabled={len(disabled)}", flush=True)
                except Exception:
                    print("Experiment guardrails unavailable", flush=True)
                await asyncio.sleep(30)
        finally:
            await engine.dispose()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit("Experiment guardrail configuration unavailable") from None


if __name__ == "__main__":
    main()
