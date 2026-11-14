"""Bounded observation HTTP adapter; persistence/provider are substituted."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI
from api.domain.media_allocation_grant import AllocationRejected
from api.domain.tenant_objects import ObjectInputError,ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.interfaces.api_controllers import media_observation_endpoint as endpoint


@pytest.mark.asyncio
async def test_bounded_observation_request_and_exact_failures(monkeypatch):
    app=FastAPI();app.include_router(endpoint.router)
    media=SimpleNamespace();app.dependency_overrides[endpoint.media_objects_service]=lambda:media
    observe=AsyncMock(return_value={'phase':2});monkeypatch.setattr(endpoint,'observe_media',observe)
    body={'object':'media_p_'+'a'*128+'_'+'b'*32,'references':[]}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://unit') as client:
        result=await client.post('/storage/media/observe',json=body)
        assert result.status_code==200 and result.json()=={'phase':2}
        assert result.headers['cache-control']=='private, no-store'
        for invalid in [{},{**body,'references':[{}]},{**body,'object':'foreign'},{**body,'repair':True}]:
            assert (await client.post('/storage/media/observe',json=invalid)).status_code==400
        assert (await client.post('/storage/media/observe',content=b'{}')).status_code==415
        assert (await client.post('/storage/media/observe',json=body,headers={'content-encoding':'gzip'})).status_code==415
        assert (await client.post('/storage/media/observe',content=b'x'*4097,headers={'content-type':'application/json'})).status_code==413
        assert observe.await_count==1
        for error,status in [(AllocationRejected(),403),(RecordConflict(),409),(ObjectInputError(),400),(ObjectUnavailable(),503),(TimeoutError(),503)]:
            observe.side_effect=error
            assert (await client.post('/storage/media/observe',json=body)).status_code==status
