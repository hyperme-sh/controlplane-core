"""Private opaque bytes for a scoped tenant service, not a public media origin."""
from functools import partial
import re
from contextlib import asynccontextmanager
from urllib.parse import urlsplit
from azure.storage.blob.aio import ContainerClient

import anyio
import boto3
from botocore.config import Config
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from api.core.container import get_storage_settings_repository
from api.domain.tenant_objects import MAX_OBJECT_BYTES, ObjectInputError, ObjectUnavailable, TenantObjects, byte_range
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.infrastructure.security.storage_session import storage_session

upload_capacity = anyio.CapacityLimiter(8)

router = APIRouter(prefix="/storage/objects", tags=["Tenant objects"])


async def object_store(request: Request):
    from api.infrastructure.security.storage_session import request_operation
    operation = request_operation(request.method, request.url.path)
    header = request.headers.get("authorization", "")
    if len(request.headers.getlist("authorization")) != 1 or not header.startswith("Bearer ") or header.count(" ") != 1:
        raise HTTPException(status_code=401, detail="Storage authentication required")
    tenant = storage_session(header[7:], operation)
    async with tenant_backend(tenant) as backend:
        yield TenantObjects(tenant, backend)


@asynccontextmanager
async def tenant_backend(tenant):
    """Deployment-owned private provider, after the caller authenticates its tenant."""
    selected = await get_storage_settings_repository().get_active_for_tenant(tenant)
    if selected is None or selected.tenant_id != tenant or not selected.is_enabled:
        raise HTTPException(status_code=503, detail="Tenant object storage unavailable")
    if selected.provider == "azure_blob":
        async with azure_store(selected) as backend:
            yield backend
        return
    if selected.provider != "s3":
        raise HTTPException(status_code=503, detail="Tenant object storage unavailable")
    fields = [selected.aws_bucket_name, selected.aws_access_key, selected.aws_secret_key, selected.region]
    if not all(isinstance(value, str) and value.strip() and value == value.strip() for value in fields):
        raise HTTPException(status_code=503, detail="Tenant object storage unavailable")
    client = await anyio.to_thread.run_sync(partial(boto3.client, "s3", region_name=selected.region,
        aws_access_key_id=selected.aws_access_key, aws_secret_access_key=selected.aws_secret_key,
        config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2})))
    try:
        try:
            block = await anyio.to_thread.run_sync(partial(client.get_public_access_block, Bucket=selected.aws_bucket_name))
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Private tenant object storage unavailable") from exc
        flags = block.get("PublicAccessBlockConfiguration", {})
        if any(flags.get(name) is not True for name in ("BlockPublicAcls", "IgnorePublicAcls", "BlockPublicPolicy", "RestrictPublicBuckets")):
            raise HTTPException(status_code=503, detail="Private tenant object storage required")
        yield S3TenantObjects(client, selected.aws_bucket_name, region=selected.region,
            access_key=selected.aws_access_key, secret_key=selected.aws_secret_key)
    finally:
        await anyio.to_thread.run_sync(client.close)


@asynccontextmanager
async def azure_store(selected):
    connection = getattr(selected, "azure_connection_string", None)
    container = getattr(selected, "azure_container_name", None)
    if not isinstance(connection, str) or not connection.strip() or not isinstance(container, str) or not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{1,61}[a-z0-9])", container):
        raise HTTPException(status_code=503, detail="Tenant object storage unavailable")
    try:
        client = ContainerClient.from_connection_string(connection, container,
            max_single_put_size=MAX_OBJECT_BYTES, max_single_get_size=65536, max_chunk_get_size=65536,
            connection_timeout=5, read_timeout=30, retry_total=2, permit_redirects=False)
        async with client:
            url = urlsplit(client.url)
            if (url.scheme != "https" or not re.fullmatch(r"[a-z0-9]{3,24}\.blob\.core\.windows\.net", url.hostname or "")
                    or url.port not in (None, 443) or url.username or url.password or url.query or url.fragment
                    or url.path != "/" + container):
                raise ObjectUnavailable("Approved HTTPS Azure storage required")
            with anyio.fail_after(30):
                policy = await client.get_container_access_policy()
            if not isinstance(policy, dict) or "public_access" not in policy or policy["public_access"] is not None:
                raise ObjectUnavailable("Private container required")
            yield AzureTenantObjects(client)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Private tenant object storage unavailable") from exc


def headers(object_id: str, size: int):
    return {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        "Content-Type": "application/octet-stream", "Content-Disposition": "attachment",
        "Content-Length": str(size), "ETag": f'"{object_id}"', "Accept-Ranges": "bytes"}


@router.put("/{object_id}")
async def put_object(object_id: str, request: Request, store: TenantObjects = Depends(object_store, scope="request")):
    if request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream" or request.headers.get("content-encoding", "identity") != "identity":
        raise HTTPException(status_code=415, detail="Opaque binary object required")
    length = request.headers.get("content-length")
    if length is None:
        raise HTTPException(status_code=411, detail="Object length required")
    if not re.fullmatch(r"[1-9][0-9]{0,8}", length) or int(length) > MAX_OBJECT_BYTES:
        raise HTTPException(status_code=413, detail="Object size limit exceeded")
    try:
        with anyio.fail_after(120):
            async with upload_capacity:
                created = await store.put(object_id, int(length), request.stream())
        return Response(status_code=201 if created else 204, headers={"Cache-Control": "no-store", "ETag": f'"{object_id}"'})
    except ObjectInputError as exc:
        raise HTTPException(status_code=400, detail="Invalid object bytes or checksum") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Tenant object storage unavailable") from exc


@router.api_route("/{object_id}", methods=["GET", "HEAD"])
async def get_object(object_id: str, request: Request, store: TenantObjects = Depends(object_store, scope="request")):
    try:
        metadata = await store.head(object_id)
    except ObjectInputError as exc:
        raise HTTPException(status_code=400, detail="Invalid object ID") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Tenant object storage unavailable") from exc
    if metadata is None:
        raise HTTPException(status_code=404, detail="Object not found")
    if request.method == "HEAD":
        return Response(headers=headers(object_id, metadata.size))
    requested = request.headers.get("range")
    if request.headers.get("if-range") not in (None, f'"{object_id}"'):
        requested = None
    try:
        start, length = byte_range(requested, metadata.size)
    except ObjectInputError as exc:
        raise HTTPException(status_code=416, detail="Invalid byte range", headers={"Content-Range": f"bytes */{metadata.size}"}) from exc
    response_headers = headers(object_id, length)
    if requested is not None:
        response_headers["Content-Range"] = f"bytes {start}-{start + length - 1}/{metadata.size}"
    return StreamingResponse(store.read(object_id, start, length), status_code=206 if requested is not None else 200, headers=response_headers)


@router.delete("/{object_id}", status_code=204)
async def delete_object(object_id: str, store: TenantObjects = Depends(object_store, scope="request")):
    try:
        await store.delete(object_id)
        return Response(status_code=204, headers={"Cache-Control": "no-store"})
    except ObjectInputError as exc:
        raise HTTPException(status_code=400, detail="Invalid object ID") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Tenant object storage unavailable") from exc
