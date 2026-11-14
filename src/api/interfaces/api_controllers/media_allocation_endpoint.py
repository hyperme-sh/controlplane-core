"""Tenant-scoped issuance also requires a signed protected-source request."""
import base64
import binascii
import json

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.domain.media_allocation_grant import AllocationRejected, _unique
from api.domain.tenant_records import RecordConflict
from api.domain.tenant_objects import ObjectUnavailable
from api.interfaces.api_controllers.tenant_records_endpoint import records_store

router=APIRouter(prefix='/storage/allocations',tags=['Media allocations'])
capacity=anyio.CapacityLimiter(4)
PRIVATE={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}


@router.post('/issue')
async def issue(request: Request, records=Depends(records_store)):
    settings=getattr(request.app.state,'media_allocation_issuers',{}).get(str(records.tenant))
    if settings is None:
        raise HTTPException(503,'Media allocation unavailable',headers=PRIVATE)
    if (request.headers.get('content-type','').split(';')[0]!='application/json'
            or request.headers.get('content-encoding','identity')!='identity'):
        raise HTTPException(415,'Expected uncompressed JSON',headers=PRIVATE)
    try:
        with anyio.fail_after(4):
            async with capacity:
                data=bytearray()
                async for chunk in request.stream():
                    if len(data)+len(chunk)>8192:
                        raise HTTPException(413,'Allocation request too large',headers=PRIVATE)
                    data.extend(chunk)
                try:
                    body=json.loads(bytes(data),object_pairs_hook=_unique)
                    if (type(body) is not dict or set(body)!={'object','owner','ownershipDigest','request'}
                            or any(type(value) is not str for value in body.values())):
                        raise ValueError()
                    proof=base64.b64decode(body['request'],validate=True)
                    if not 1<=len(proof)<=4096 or base64.b64encode(proof).decode('ascii')!=body['request']:
                        raise ValueError()
                except (ValueError,TypeError,RecursionError,binascii.Error):
                    raise HTTPException(400,'Invalid allocation request',headers=PRIVATE) from None
                if await request.is_disconnected():
                    raise HTTPException(408,'Allocation request cancelled',headers=PRIVATE)
                raw=await settings.bind(records.backend).issue(proof,object=body['object'],owner=body['owner'],
                    ownership_digest=body['ownershipDigest'],cancel=lambda:None)
                if await request.is_disconnected():
                    raise HTTPException(408,'Allocation request cancelled',headers=PRIVATE)
                return Response(raw,media_type='application/json',headers=PRIVATE)
    except AllocationRejected:
        raise HTTPException(403,'Media allocation refused',headers=PRIVATE) from None
    except RecordConflict:
        raise HTTPException(409,'Media allocation conflict; reconcile',headers=PRIVATE) from None
    except (TimeoutError,ObjectUnavailable):
        raise HTTPException(503,'Media allocation unavailable',headers=PRIVATE) from None
