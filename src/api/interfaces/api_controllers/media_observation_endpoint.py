"""Separately scoped, bounded read-only media metadata observation."""
import json
import anyio
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from api.domain.media_allocation_grant import AllocationRejected, _unique
from api.infrastructure.persistence.media_observation import observe_media, observation_request
from api.domain.tenant_objects import ObjectInputError, ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.interfaces.api_controllers.media_retirement_endpoint import media_objects_service, PRIVATE

router = APIRouter(prefix='/storage/media', tags=['Media observation'])
capacity = anyio.CapacityLimiter(2)


async def observation_capacity():
    # Refuse excess work before database/provider dependencies are evaluated.
    try:
        capacity.acquire_nowait()
    except anyio.WouldBlock:
        raise HTTPException(503, 'Media observation busy', headers=PRIVATE) from None
    try:
        yield
    finally:
        capacity.release()


@router.post('/observe')
async def observe(request: Request, media=Depends(media_objects_service)):
    if (request.headers.get('content-type', '').split(';')[0] != 'application/json' or
            request.headers.get('content-encoding', 'identity') != 'identity'):
        raise HTTPException(415, 'Expected uncompressed JSON', headers=PRIVATE)
    try:
        with anyio.fail_after(60):
            raw = bytearray()
            with anyio.fail_after(1):
                async for chunk in request.stream():
                    if len(raw) + len(chunk) > 4096:
                        raise HTTPException(413, 'Observation request too large', headers=PRIVATE)
                    raw.extend(chunk)
            try:
                body = json.loads(raw, object_pairs_hook=_unique)
                if type(body) is not dict or set(body) not in (
                        {'object', 'references'}, {'object', 'references', 'includeAllocation'}):
                    raise ValueError()
                if 'includeAllocation' in body and type(body['includeAllocation']) is not bool:
                    raise ValueError()
                observation_request(body['object'], body['references'])
            except (ValueError, TypeError, RecursionError):
                raise HTTPException(400, 'Invalid observation request', headers=PRIVATE) from None
            if await request.is_disconnected():
                raise HTTPException(408, 'Observation cancelled', headers=PRIVATE)
            result = await observe_media(media, object=body['object'], references=body['references'],
                                         guard=lambda: None, include_allocation=body.get('includeAllocation', False))
            if await request.is_disconnected():
                raise HTTPException(408, 'Observation cancelled', headers=PRIVATE)
            return JSONResponse(result, headers=PRIVATE)
    except AllocationRejected:
        raise HTTPException(403, 'Media observation refused', headers=PRIVATE) from None
    except RecordConflict:
        raise HTTPException(409, 'Media observation conflict', headers=PRIVATE) from None
    except ObjectInputError:
        raise HTTPException(400, 'Invalid observation request', headers=PRIVATE) from None
    except (ObjectUnavailable, TimeoutError):
        raise HTTPException(503, 'Media observation unavailable', headers=PRIVATE) from None
