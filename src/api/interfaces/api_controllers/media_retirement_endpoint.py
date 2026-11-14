"""Narrow source-authenticated retirement transport for PostgreSQL media."""
import base64
import binascii
import json
import time

import anyio
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import JSONResponse
from api.domain.media_allocation_grant import AllocationRejected,_unique
from api.domain.media_retirement import MediaRetirement
from api.domain.tenant_objects import ObjectInputError,ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.infrastructure.persistence.media_retirement_postgres import PostgresMediaRetirement
from api.infrastructure.persistence.media_object_postgres import PostgresMediaObjects
from api.infrastructure.externals.media_retirement_backend import AzureMediaObjects,S3MediaObjects
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects
from api.interfaces.api_controllers.tenant_objects_endpoint import object_store

router=APIRouter(prefix='/storage/media',tags=['Media retirement'])
capacity=anyio.CapacityLimiter(4)
PRIVATE={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}


def media_objects_service(request:Request,objects=Depends(object_store,scope='request')):
    settings=getattr(request.app.state,'media_allocation_issuers',{}).get(str(objects.tenant))
    if settings is None or settings.registry is None:
        raise HTTPException(503,'Media retirement unavailable',headers=PRIVATE)
    existing=objects.backend
    if isinstance(existing,AzureTenantObjects):backend=AzureMediaObjects(existing.container)
    elif isinstance(existing,S3TenantObjects):
        backend=S3MediaObjects(existing.client,existing.bucket,region=existing.region,
            access_key=existing.credentials.access_key,secret_key=existing.credentials.secret_key)
    else:raise HTTPException(503,'Media retirement unavailable',headers=PRIVATE)
    store=settings.registry.store(require_locations=True)
    return PostgresMediaObjects(store,backend)


def retirement_service(request:Request,objects=Depends(object_store,scope='request')):
    media=media_objects_service(request,objects)
    settings=request.app.state.media_allocation_issuers[str(objects.tenant)]
    authority=PostgresMediaRetirement(media.store,source=settings.source,request_keys=settings.request_keys,clock=lambda:int(time.time()))
    return MediaRetirement(authority,media)


@router.post('/retire')
async def retire(request:Request,service=Depends(retirement_service)):
    if (request.headers.get('content-type','').split(';')[0]!='application/json'
            or request.headers.get('content-encoding','identity')!='identity'):
        raise HTTPException(415,'Expected uncompressed JSON',headers=PRIVATE)
    try:
        with anyio.fail_after(60):
            async with capacity:
                data=bytearray()
                with anyio.fail_after(1):
                    async for chunk in request.stream():
                        if len(data)+len(chunk)>8192:raise HTTPException(413,'Retirement request too large',headers=PRIVATE)
                        data.extend(chunk)
                try:
                    body=json.loads(bytes(data),object_pairs_hook=_unique)
                    if (type(body) is not dict or set(body)!={'object','owner','command','request'}
                            or any(type(value) is not str for value in body.values())):raise ValueError()
                    proof=base64.b64decode(body['request'],validate=True)
                    if not 1<=len(proof)<=4096 or base64.b64encode(proof).decode('ascii')!=body['request']:raise ValueError()
                except (ValueError,TypeError,RecursionError,binascii.Error):
                    raise HTTPException(400,'Invalid retirement request',headers=PRIVATE) from None
                if await request.is_disconnected():raise HTTPException(408,'Retirement request cancelled',headers=PRIVATE)
                result=await service.retire(proof,object=body['object'],owner=body['owner'],command=body['command'],cancel=lambda:None)
                if await request.is_disconnected():raise HTTPException(408,'Retirement request cancelled',headers=PRIVATE)
                return JSONResponse(result,headers=PRIVATE)
    except AllocationRejected:
        raise HTTPException(403,'Media retirement refused',headers=PRIVATE) from None
    except RecordConflict:
        raise HTTPException(409,'Media retirement conflict',headers=PRIVATE) from None
    except ObjectInputError:
        raise HTTPException(400,'Invalid retirement request',headers=PRIVATE) from None
    except (TimeoutError,ObjectUnavailable):
        raise HTTPException(503,'Media cleanup unconfirmed; retry the accepted command',headers=PRIVATE) from None
