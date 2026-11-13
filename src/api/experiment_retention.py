"""Private experiment expiry worker; no HTTP deletion or signing authority."""
import asyncio
import json
import os
import time
from functools import partial
from uuid import UUID

import anyio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from controlplane_api.config import settings
from controlplane_db.session import normalize_database_url
from api.domain.experiments.records import AUDIENCES
from api.domain.experiments.retention import sweep_page
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects
from api.infrastructure.externals.tenant_record_azure import AzureTenantRecords
from api.infrastructure.externals.tenant_record_s3 import S3TenantRecords
from api.infrastructure.persistence.db_session import set_db_session, reset_db_session
from api.infrastructure.persistence.tenant_guc import set_current_tenant_id_guc
from api.interfaces.api_controllers.tenant_objects_endpoint import tenant_backend


def listing(objects):
    if isinstance(objects, AzureTenantObjects):
        records = AzureTenantRecords(objects.container)
        async def page(prefix, token):
            pages = objects.container.list_blobs(name_starts_with=prefix, results_per_page=1000).by_page(continuation_token=token)
            try:
                rows = await anext(pages)
            except StopAsyncIteration:
                return [], None
            names = []
            async for row in rows:
                names.append(row.name)
                if len(names) > 1000:
                    raise ValueError("Retention page exceeds limit")
            return names, pages.continuation_token
        return records, page
    if isinstance(objects, S3TenantObjects):
        records = S3TenantRecords(objects)
        async def page(prefix, token):
            arguments = {"Bucket": objects.bucket, "Prefix": prefix, "MaxKeys": 1000}
            if token is not None:
                arguments["ContinuationToken"] = token
            result = await anyio.to_thread.run_sync(partial(objects.client.list_objects_v2, **arguments))
            if not isinstance(result, dict) or type(result.get("IsTruncated")) is not bool:
                raise ValueError("Invalid retention page")
            rows = result.get("Contents", [])
            if not isinstance(rows, list) or len(rows) > 1000:
                raise ValueError("Invalid retention page")
            next_token = result.get("NextContinuationToken") if result["IsTruncated"] else None
            if result["IsTruncated"] and not next_token:
                raise ValueError("Missing retention continuation")
            return [row["Key"] for row in rows], next_token
        return records, page
    raise ValueError("Private experiment provider required")


async def tick(maker, tenant, audience, now_ms):
    async with maker() as session:
        context = set_db_session(session)
        try:
            await set_current_tenant_id_guc(session, tenant)
            async with tenant_backend(tenant) as objects:
                records, page = listing(objects)
                result = await sweep_page(records, page, tenant, audience, now_ms)
            await session.commit()
            return result
        except BaseException:
            await session.rollback()
            raise
        finally:
            reset_db_session(context)


async def run():
    tenant = UUID(os.environ["EXPERIMENT_TENANT_ID"])
    audience = os.environ["EXPERIMENT_RETENTION_AUDIENCE"]
    if tenant.int == 0 or audience not in AUDIENCES:
        raise ValueError("Explicit retention binding required")
    engine = create_async_engine(normalize_database_url(settings.database_url))
    maker = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    try:
        while True:
            more = False
            try:
                with anyio.fail_after(120):
                    result = await tick(maker, tenant, audience, int(time.time() * 1000))
                more = result["more"]
                print(json.dumps(result), flush=True)
            except Exception:
                print("Experiment retention unavailable", flush=True)
            # Finish a paginated scan promptly; never restart each page at the
            # first live cohort and starve expired records later in the listing.
            await asyncio.sleep(0 if more else 60)
    finally:
        await engine.dispose()


def main():
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    except Exception:
        raise SystemExit("Experiment retention configuration unavailable") from None


if __name__ == "__main__":
    main()
