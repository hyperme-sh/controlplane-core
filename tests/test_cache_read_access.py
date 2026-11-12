"""Mocked Azure and HTTP contracts for delegated cache reads."""
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from azure.core.exceptions import ResourceNotFoundError
from azure.storage.blob.aio import ContainerClient
from fastapi import FastAPI
import httpx
import pytest

from api.domain.tenant_objects import ObjectUnavailable
from api.domain.tenant_records import HEADER_BYTES, MAX_STORED_BYTES, TenantRecords, record_key
from api.infrastructure.externals.tenant_record_azure import AzureTenantRecords
from api.interfaces.api_controllers import tenant_records_endpoint as route
from api.infrastructure.security.storage_session import request_operation


def backend(size=100):
    container = ContainerClient.from_connection_string(
        'DefaultEndpointsProtocol=https;AccountName=cachetest;AccountKey=YWJj;EndpointSuffix=core.windows.net', 'private-cache')
    original = container.get_blob_client
    blob = SimpleNamespace(url='', get_blob_properties=AsyncMock(return_value=SimpleNamespace(size=size, etag='"revision"')),
        download_blob=AsyncMock(side_effect=AssertionError('Must not proxy cache bytes')))
    def selected(key):
        blob.url = original(key).url
        return blob
    container.get_blob_client = selected
    return AzureTenantRecords(container), blob


@pytest.mark.asyncio
async def test_grant_is_one_tenant_blob_read_only_https_and_short_lived():
    store, blob = backend()
    key = record_key(uuid4(), 'cache', 'public-feed')
    before = int(datetime.now(timezone.utc).timestamp())
    grant = await store.read_access(key)
    parsed = urlsplit(grant['url'])
    sas = parse_qs(parsed.query)
    assert parsed.hostname == 'cachetest.blob.core.windows.net'
    assert parsed.path == '/private-cache/' + key
    assert sas['sp'] == ['r'] and sas['sr'] == ['b'] and sas['spr'] == ['https']
    assert sas['rscc'] == ['private, no-store']
    assert 'YWJj' not in grant['url']
    assert before + 59 <= grant['expires_at'] <= before + 61
    assert grant['headers'] == {'If-Match': '"revision"'}
    assert grant['size'] == 100 and grant['encoding'] == 'spanrec1'
    blob.download_blob.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [0, HEADER_BYTES, MAX_STORED_BYTES + 1, True, '100'])
async def test_invalid_length_is_not_delegated(size):
    store, _ = backend(size)
    with pytest.raises(ObjectUnavailable):
        await store.read_access('key')


@pytest.mark.asyncio
@pytest.mark.parametrize('code', ['BlobNotFound', 'ContainerNotFound', 'AuthenticationFailed'])
async def test_only_missing_blob_is_absent(code):
    store, blob = backend()
    error = ResourceNotFoundError('secret provider details')
    error.error_code = code
    blob.get_blob_properties.side_effect = error
    if code == 'BlobNotFound':
        assert await store.read_access('key') is None
    else:
        with pytest.raises(ObjectUnavailable, match='^Record storage unavailable$'):
            await store.read_access('key')


@pytest.mark.asyncio
async def test_endpoint_is_cache_only_and_preserves_absence_and_unsupported():
    adapter, _ = backend()
    adapter.read_access = AsyncMock(return_value={'url': 'https://example.invalid/short-lived'})
    tenant = uuid4()
    store = TenantRecords(tenant, adapter)
    app = FastAPI()
    app.include_router(route.router, prefix='/api/v1')
    app.dependency_overrides[route.records_store] = lambda: store
    path = '/api/v1/storage/records/cache/public-feed/access'
    assert request_operation('GET', path) == 'records.read'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://storage') as client:
        result = await client.get(path)
        assert result.status_code == 200
        assert result.headers['cache-control'] == 'private, no-store'
        assert result.headers['referrer-policy'] == 'no-referrer'
        adapter.read_access.assert_awaited_once_with(record_key(tenant, 'cache', 'public-feed'))
        for namespace in ('recovery', 'fixtures'):
            assert (await client.get(path.replace('/cache/', '/' + namespace + '/'))).status_code == 400
        assert (await client.get(path.replace('public-feed', 'bad.name'))).status_code == 400
        adapter.read_access.return_value = None
        assert (await client.get(path)).status_code == 404
        adapter.read_access.side_effect = ObjectUnavailable('private details')
        result = await client.get(path)
        assert result.status_code == 503 and 'private details' not in result.text
        store.backend = object()
        assert (await client.get(path)).status_code == 501
