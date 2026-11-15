"""Private group wrap bytes for narrowly scoped tenant services."""
import re
import anyio
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from api.domain.group_allocation_grant import AllocationRejected
from api.domain.tenant_objects import ObjectInputError,ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.infrastructure.persistence.group_object_postgres import PostgresGroupObjects
from api.infrastructure.externals.group_retirement_backend import AzureGroupObjects,S3GroupObjects
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects
from api.interfaces.api_controllers.tenant_objects_endpoint import object_store

PRIVATE={'Cache-Control':'private, no-store','X-Content-Type-Options':'nosniff'}
capacity=anyio.CapacityLimiter(4)


async def group_payload_capacity():
    try:capacity.acquire_nowait()
    except anyio.WouldBlock:
        raise HTTPException(503,'Group storage busy',headers=PRIVATE) from None
    try:
        try:
            with anyio.fail_after(8):yield
        except TimeoutError:
            raise HTTPException(503,'Group storage unavailable',headers=PRIVATE) from None
    finally:capacity.release()


router=APIRouter(prefix='/storage/groups/wraps',tags=['Private group wraps'],
    dependencies=[Depends(group_payload_capacity,scope='request')])


def group_objects_service(request:Request,objects=Depends(object_store,scope='request')):
    settings=getattr(request.app.state,'group_allocation_issuers',{}).get(str(objects.tenant))
    if settings is None or settings.registry is None:
        raise HTTPException(503,'Group storage unavailable',headers=PRIVATE)
    existing=objects.backend
    if isinstance(existing,AzureTenantObjects):backend=AzureGroupObjects(existing.container)
    elif isinstance(existing,S3TenantObjects):
        backend=S3GroupObjects(existing.client,existing.bucket,region=existing.region,
            access_key=existing.credentials.access_key,secret_key=existing.credentials.secret_key)
    else:raise HTTPException(503,'Group storage unavailable',headers=PRIVATE)
    return PostgresGroupObjects(settings.registry.store(require_locations=True),backend)


def valid_path(set_id,digest):
    if not re.fullmatch(r'set_p_[a-f0-9]{128}_[a-f0-9]{32}',set_id) or not re.fullmatch('[a-f0-9]{64}',digest):
        raise HTTPException(400,'Invalid group wrap reference',headers=PRIVATE)


def refusal(error):
    if isinstance(error,AllocationRejected):return HTTPException(403,'Group allocation refused',headers=PRIVATE)
    if isinstance(error,RecordConflict):return HTTPException(409,'Group allocation conflict',headers=PRIVATE)
    if isinstance(error,ObjectInputError):return HTTPException(400,'Invalid group wrap bytes',headers=PRIVATE)
    return HTTPException(503,'Group storage unavailable',headers=PRIVATE)


@router.put('/{set_id}/{digest}')
async def upload(set_id:str,digest:str,request:Request,store=Depends(group_objects_service)):
    valid_path(set_id,digest)
    if (request.headers.get('content-type','').split(';')[0]!='application/octet-stream' or
            request.headers.get('content-encoding','identity')!='identity'):
        raise HTTPException(415,'Expected opaque bytes',headers=PRIVATE)
    owners=request.headers.getlist('x-group-owner');lengths=request.headers.getlist('content-length')
    if len(owners)!=1 or re.fullmatch(r'[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}',owners[0]) is None:
        raise HTTPException(400,'Group owner required',headers=PRIVATE)
    if len(lengths)!=1 or re.fullmatch('[1-9][0-9]{0,4}',lengths[0]) is None:
        raise HTTPException(411,'Group wrap length required',headers=PRIVATE)
    size=int(lengths[0])
    if size>65536:raise HTTPException(413,'Group wrap too large',headers=PRIVATE)
    try:
        created=await store.put(object=set_id,owner=owners[0],digest=digest,size=size,
            chunks=request.stream(),guard=lambda:None)
        if await request.is_disconnected():raise HTTPException(408,'Group upload cancelled',headers=PRIVATE)
        return Response(status_code=201 if created else 204,headers={**PRIVATE,'ETag':f'"{digest}"'})
    except (AllocationRejected,RecordConflict,ObjectInputError,ObjectUnavailable) as error:
        raise refusal(error) from None


@router.get('/{set_id}/{digest}')
async def download(set_id:str,digest:str,request:Request,store=Depends(group_objects_service)):
    valid_path(set_id,digest)
    try:
        async with store.open(object=set_id,digest=digest,guard=lambda:None) as (body,metadata):
            if await request.is_disconnected():raise HTTPException(408,'Group download cancelled',headers=PRIVATE)
            data=body.read(65537)
            if not 1<=len(data)<=65536 or len(data)!=metadata.size:
                raise ObjectUnavailable('Group wrap unavailable')
            return Response(data,media_type='application/octet-stream',headers={**PRIVATE,
                'ETag':f'"{digest}"','Content-Disposition':'attachment'})
    except (AllocationRejected,RecordConflict,ObjectInputError,ObjectUnavailable) as error:
        raise refusal(error) from None
