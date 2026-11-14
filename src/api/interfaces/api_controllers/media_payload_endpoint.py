"""Private media upload tied to a durable allocation and provider namespace."""
import re
import anyio
from fastapi import APIRouter,Depends,HTTPException,Request,Response
from fastapi.responses import StreamingResponse
from api.domain.media_allocation_grant import AllocationRejected
from api.domain.tenant_objects import ObjectInputError,ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.interfaces.api_controllers.media_retirement_endpoint import media_objects_service,PRIVATE

router=APIRouter(prefix='/storage/media/payloads',tags=['Media payloads'])
capacity=anyio.CapacityLimiter(4)


@router.put('/{object}/{kind}/{digest}')
async def upload(object:str,kind:str,digest:str,request:Request,store=Depends(media_objects_service)):
    if (request.headers.get('content-type','').split(';')[0]!='application/octet-stream'
            or request.headers.get('content-encoding','identity')!='identity'):
        raise HTTPException(415,'Expected opaque bytes',headers=PRIVATE)
    owners=request.headers.getlist('x-media-owner')
    lengths=request.headers.getlist('content-length')
    if len(owners)!=1 or re.fullmatch(r'[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}',owners[0]) is None:
        raise HTTPException(400,'Media owner required',headers=PRIVATE)
    if len(lengths)!=1 or re.fullmatch(r'[1-9][0-9]{0,7}',lengths[0]) is None:
        raise HTTPException(411,'Media length required',headers=PRIVATE)
    size=int(lengths[0])
    if kind not in ('ciphertext','wrap') or re.fullmatch('[0-9a-f]{64}',digest) is None:
        raise HTTPException(400,'Invalid media reference',headers=PRIVATE)
    if not (33 if kind=='ciphertext' else 1)<=size<=(64*1024*1024 if kind=='ciphertext' else 65536):
        raise HTTPException(413,'Media size limit exceeded',headers=PRIVATE)
    try:
        with anyio.fail_after(120):
            async with capacity:
                created=await store.put(object=object,owner=owners[0],kind=kind,digest=digest,size=size,
                    chunks=request.stream(),guard=lambda:None)
                if await request.is_disconnected():
                    raise HTTPException(408,'Media upload cancelled',headers=PRIVATE)
        return Response(status_code=201 if created else 204,headers={**PRIVATE,'ETag':f'"{digest}"'})
    except AllocationRejected:
        raise HTTPException(403,'Media allocation or quota refused',headers=PRIVATE) from None
    except RecordConflict:
        raise HTTPException(409,'Media allocation conflict',headers=PRIVATE) from None
    except ObjectInputError:
        raise HTTPException(400,'Invalid media bytes or checksum',headers=PRIVATE) from None
    except (TimeoutError,ObjectUnavailable):
        raise HTTPException(503,'Media upload unconfirmed',headers=PRIVATE) from None

async def payload_reader(object:str,kind:str,digest:str,request:Request,store=Depends(media_objects_service)):
    try:
        with anyio.fail_after(120):
            async with capacity:
                async with store.open(object=object,kind=kind,digest=digest,guard=lambda:None) as held:
                    if await request.is_disconnected():
                        raise HTTPException(408,'Media download cancelled',headers=PRIVATE)
                    yield held
    except AllocationRejected:
        raise HTTPException(403,'Media reference refused',headers=PRIVATE) from None
    except RecordConflict:
        raise HTTPException(409,'Media allocation conflict',headers=PRIVATE) from None
    except ObjectInputError:
        raise HTTPException(400,'Invalid media reference',headers=PRIVATE) from None
    except (TimeoutError,ObjectUnavailable):
        raise HTTPException(503,'Media download unavailable',headers=PRIVATE) from None


@router.get('/{object}/{kind}/{digest}')
async def download(digest:str,held=Depends(payload_reader,scope='request')):
    body,metadata=held
    async def chunks():
        while True:
            chunk=await anyio.to_thread.run_sync(body.read,65536)
            if not chunk:break
            yield chunk
    return StreamingResponse(chunks(),media_type='application/octet-stream',headers={
        **PRIVATE,'Content-Length':str(metadata.size),'ETag':f'"{digest}"','Content-Disposition':'attachment'})
