"""CI contracts for allocation-bound uploads and narrow credentials."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI,HTTPException
from api.interfaces.api_controllers import media_payload_endpoint as endpoint
from api.domain.media_allocation_grant import AllocationRejected
from api.domain.tenant_records import RecordConflict
from api.domain.tenant_objects import ObjectInputError,ObjectUnavailable

OWNER='a'*43+'.'+'b'*43
OBJECT='media_p_'+'a'*128+'_'+'b'*32
DIGEST='c'*64
PATH=f'/storage/media/payloads/{OBJECT}/ciphertext/{DIGEST}'
HEADERS={'content-type':'application/octet-stream','x-media-owner':OWNER}


@pytest.mark.asyncio
async def test_upload_exact_reference_bounds_and_safe_failure_mapping():
    app=FastAPI();app.include_router(endpoint.router)
    bodies=[]
    async def put(**kwargs):
        bodies.append((kwargs,{key:kwargs[key] for key in ('object','owner','kind','digest','size')}))
        assert b''.join([chunk async for chunk in kwargs['chunks']])==b'x'*33
        kwargs['guard']()
        return len(bodies)==1
    store=SimpleNamespace(put=AsyncMock(side_effect=put))
    app.dependency_overrides[endpoint.media_objects_service]=lambda:store
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='https://storage') as client:
        for status in (201,204):
            result=await client.put(PATH,content=b'x'*33,headers=HEADERS)
            assert result.status_code==status and result.headers['etag']==f'"{DIGEST}"'
            assert result.headers['cache-control']=='private, no-store'
        assert bodies[0][1]==dict(object=OBJECT,owner=OWNER,kind='ciphertext',digest=DIGEST,size=33)
        for headers,status in [({**HEADERS,'content-type':'text/plain'},415),
            ({**HEADERS,'content-encoding':'gzip'},415),({**HEADERS,'x-media-owner':'bad'},400),
            ({**HEADERS,'content-length':'0'},411),({**HEADERS,'content-length':'67108865'},413)]:
            assert (await client.put(PATH,content=b'x'*33,headers=headers)).status_code==status
        assert (await client.put(PATH,content=b'x',headers=HEADERS)).status_code==413
        for path in [PATH.replace('/ciphertext/','/invalid/'),PATH.replace(DIGEST,'invalid')]:
            assert (await client.put(path,content=b'x'*33,headers=HEADERS)).status_code==400
        assert store.put.await_count==2
        for error,status in [(AllocationRejected(),403),(RecordConflict(),409),(ObjectInputError(),400),(ObjectUnavailable(),503),(TimeoutError(),503)]:
            store.put.side_effect=error
            result=await client.put(PATH,content=b'x'*33,headers=HEADERS)
            assert result.status_code==status and result.headers['cache-control']=='private, no-store'
        store.put.side_effect=None;store.put.return_value=True
        assert (await client.put(PATH.replace('/ciphertext/','/wrap/'),content=b'x',headers=HEADERS)).status_code==201
