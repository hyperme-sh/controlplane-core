"""Private tenant-admin experiment configuration; this endpoint publishes no flags."""
from typing import Annotated, Literal
import re

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import ValidationError

from api.domain.experiments.control import Configuration
from api.domain.experiments.store import ExperimentMissing, ExperimentStore
from api.domain.tenant_records import RecordConflict
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects
from api.infrastructure.externals.tenant_record_azure import AzureTenantRecords
from api.infrastructure.externals.tenant_record_s3 import S3TenantRecords
from api.infrastructure.security.storage_admin import StorageAdmin, storage_admin
from api.interfaces.api_controllers.tenant_objects_endpoint import tenant_backend

router = APIRouter(tags=["Tenant experiment configuration"])
Admin = Annotated[StorageAdmin, Depends(storage_admin)]
Audience = Literal["s3r.ch", "hyperme.sh", "portal"]
capacity = anyio.CapacityLimiter(4)


async def configured_store(admin: Admin):
    async with tenant_backend(admin.tenant) as backend:
        if isinstance(backend, AzureTenantObjects):
            records = AzureTenantRecords(backend.container)
        elif isinstance(backend, S3TenantObjects):
            records = S3TenantRecords(backend)
        else:
            raise HTTPException(503, "Experiment storage unavailable")
        yield ExperimentStore(admin.tenant, records)


Store = Annotated[ExperimentStore, Depends(configured_store, scope="request")]


def response(value: Configuration, status=200):
    return Response(value.model_dump_json(), status_code=status, media_type="application/json",
        headers={"Cache-Control": "private, no-store", "ETag": f'"{value.sequence}"',
                 "X-Content-Type-Options": "nosniff"})


def precondition(request: Request):
    matches, absent = request.headers.getlist("if-match"), request.headers.getlist("if-none-match")
    if absent == ["*"] and not matches:
        return None
    if len(matches) == 1 and not absent and re.fullmatch(r'"[1-9][0-9]{0,15}"', matches[0]):
        value = int(matches[0][1:-1])
        if value <= 9007199254740991:
            return value
    raise HTTPException(428, "One exact configuration precondition required")


@router.get("/{audience}")
async def get_configuration(audience: Audience, admin: Admin, store: Store):
    try:
        with anyio.fail_after(30):
            async with capacity:
                return response(await store.load(admin.tenant, audience))
    except ExperimentMissing:
        raise HTTPException(404, "Experiment configuration not found") from None
    except Exception:
        raise HTTPException(503, "Experiment configuration unavailable") from None


@router.put("/{audience}")
async def put_configuration(audience: Audience, request: Request, admin: Admin, store: Store):
    expected = precondition(request)
    if (request.headers.get("content-type", "").split(";")[0].strip().lower() != "application/json"
            or request.headers.get("content-encoding", "identity") != "identity"):
        raise HTTPException(415, "JSON configuration required")
    lengths = request.headers.getlist("content-length")
    if len(lengths) > 1 or (lengths and not re.fullmatch(r"[0-9]{1,10}", lengths[0])):
        raise HTTPException(400, "Invalid configuration length")
    if lengths and int(lengths[0]) > 32768:
        raise HTTPException(413, "Experiment configuration exceeds limit")
    raw = bytearray()
    try:
        with anyio.fail_after(30):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 32768:
                    raise HTTPException(413, "Experiment configuration exceeds limit")
                raw.extend(chunk)
    except TimeoutError:
        raise HTTPException(408, "Configuration read timed out") from None
    try:
        value = Configuration.model_validate_json(bytes(raw))
    except ValidationError:
        raise HTTPException(400, "Invalid experiment configuration") from None
    if value.tenant != admin.tenant or value.audience != audience:
        raise HTTPException(403, "Experiment configuration scope mismatch")
    if value.sequence != (1 if expected is None else expected + 1):
        raise HTTPException(409, "Experiment configuration sequence mismatch")
    try:
        with anyio.fail_after(30):
            async with capacity:
                if expected is None:
                    await store.initialize(value)
                else:
                    previous = await store.load(admin.tenant, audience)
                    if previous.sequence != expected or not await store.replace(previous, value):
                        raise RecordConflict()
        return response(value, 201 if expected is None else 200)
    except (RecordConflict, ExperimentMissing):
        raise HTTPException(412, "Experiment configuration changed") from None
    except Exception:
        raise HTTPException(503, "Experiment configuration unavailable") from None
