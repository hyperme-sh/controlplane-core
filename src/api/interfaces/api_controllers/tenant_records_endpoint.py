"""Tenant cache/recovery/fixture records; revisions are required for mutation."""
import re

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from api.domain.tenant_objects import ObjectInputError, ObjectUnavailable
from api.domain.tenant_records import MAX_RECORD_BYTES, RecordConflict, TenantRecords, record_key
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects
from api.infrastructure.externals.tenant_record_azure import AzureTenantRecords
from api.infrastructure.externals.tenant_record_s3 import S3TenantRecords
from api.interfaces.api_controllers.tenant_objects_endpoint import object_store

router = APIRouter(prefix="/storage/records", tags=["Tenant records"])
capacity = anyio.CapacityLimiter(4)


def records_store(objects=Depends(object_store, scope="request")):
    if isinstance(objects.backend, AzureTenantObjects):
        backend = AzureTenantRecords(objects.backend.container)
    elif isinstance(objects.backend, S3TenantObjects):
        backend = S3TenantRecords(objects.backend)
    else:
        raise HTTPException(503, "Record storage unavailable")
    return TenantRecords(objects.tenant, backend)


def precondition(request: Request, *, create: bool) -> str | None:
    matches, absent = request.headers.getlist("if-match"), request.headers.getlist("if-none-match")
    if create and not matches and absent == ["*"]:
        return None
    if len(matches) == 1 and not absent and re.fullmatch(r'"[a-f0-9]{64}"', matches[0]):
        return matches[0][1:-1]
    raise HTTPException(428, "One exact record precondition required")


def headers(revision):
    return {"ETag": f'"{revision}"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}


def translate(error):
    if isinstance(error, RecordConflict):
        return HTTPException(412, "Record changed")
    if isinstance(error, ObjectInputError):
        return HTTPException(400, "Invalid record request")
    if isinstance(error, TimeoutError):
        return HTTPException(504, "Record operation timed out")
    return HTTPException(503, "Record storage unavailable")


@router.get("/{namespace}/{name}/access")
async def get_cache_read_access(namespace: str, name: str, store=Depends(records_store)):
    # Reuse the signed tenant and records:read scope. Never delegate recovery,
    # fixture, write, list, or account access through this cache-only endpoint.
    if namespace != "cache":
        raise HTTPException(400, "Cache record required")
    try:
        key = record_key(store.tenant, namespace, name)
        if not isinstance(store.backend, AzureTenantRecords):
            raise HTTPException(501, "Direct cache reads unavailable")
        with anyio.fail_after(60):
            async with capacity:
                access = await store.backend.read_access(key)
        if access is None:
            raise HTTPException(404, "Record not found")
        return JSONResponse(access, headers={"Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
    except (ObjectInputError, ObjectUnavailable, RecordConflict, TimeoutError) as exc:
        raise translate(exc) from None


@router.get("/{namespace}/{name}")
async def get_record(namespace: str, name: str, store=Depends(records_store)):
    try:
        with anyio.fail_after(60):
            async with capacity:
                record = await store.get(namespace, name)
        if record is None:
            raise HTTPException(404, "Record not found")
        return Response(record.data, media_type="application/octet-stream", headers=headers(record.revision))
    except (ObjectInputError, ObjectUnavailable, RecordConflict, TimeoutError) as exc:
        raise translate(exc) from None


@router.put("/{namespace}/{name}")
async def put_record(namespace: str, name: str, request: Request, store=Depends(records_store)):
    expected = precondition(request, create=True)
    if (request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/octet-stream"
            or request.headers.get("content-encoding", "identity") != "identity"):
        raise HTTPException(415, "Opaque binary record required")
    lengths = request.headers.getlist("content-length")
    maximum = 65536 if namespace == "experiments" else MAX_RECORD_BYTES
    if len(lengths) != 1 or not re.fullmatch(r"[1-9][0-9]{0,7}", lengths[0]) or int(lengths[0]) > maximum:
        raise HTTPException(413, "Bounded record length required")
    size = int(lengths[0])
    try:
        with anyio.fail_after(60):
            async with capacity:
                data = bytearray()
                async for chunk in request.stream():
                    if len(data) + len(chunk) > size:
                        raise ObjectInputError("Record length differs")
                    data.extend(chunk)
                if len(data) != size:
                    raise ObjectInputError("Record length differs")
                record = await store.put(namespace, name, bytes(data), expected)
        return Response(status_code=201 if expected is None else 204, headers=headers(record.revision))
    except (ObjectInputError, ObjectUnavailable, RecordConflict, TimeoutError) as exc:
        raise translate(exc) from None


@router.delete("/{namespace}/{name}")
async def delete_record(namespace: str, name: str, request: Request, store=Depends(records_store)):
    expected = precondition(request, create=False)
    try:
        with anyio.fail_after(60):
            async with capacity:
                await store.delete(namespace, name, expected)
        return Response(status_code=204, headers={"Cache-Control": "private, no-store"})
    except (ObjectInputError, ObjectUnavailable, RecordConflict, TimeoutError) as exc:
        raise translate(exc) from None
