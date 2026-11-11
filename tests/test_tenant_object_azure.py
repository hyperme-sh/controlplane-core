"""Azure tenant backend unit contracts; no service or provider calls."""
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anyio
import pytest
from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from fastapi import HTTPException

from api.domain.tenant_objects import MAX_OBJECT_BYTES, ObjectMetadata, ObjectUnavailable
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.interfaces.api_controllers import tenant_objects_endpoint as endpoint


def missing(code):
    error = ResourceNotFoundError("private-provider-details")
    error.error_code = code
    return error


@pytest.mark.asyncio
async def test_azure_atomic_upload_retry_metadata_range_and_delete():
    data = b"opaque-ciphertext"
    digest = hashlib.sha256(data).hexdigest()
    async def chunks():
        yield data[2:4]
        yield data[4:6]
    download = SimpleNamespace(size=4, chunks=chunks)
    blob = SimpleNamespace(upload_blob=AsyncMock(), get_blob_properties=AsyncMock(return_value=SimpleNamespace(size=len(data), metadata={"sha256": digest})),
        download_blob=AsyncMock(return_value=download), delete_blob=AsyncMock())
    container = SimpleNamespace(get_blob_client=Mock(return_value=blob))
    backend = AzureTenantObjects(container)
    assert await backend.put("tenant-key", BytesIO(data), ObjectMetadata(len(data), digest))
    args = blob.upload_blob.call_args.kwargs
    assert args["overwrite"] is False and args["blob_type"] == "BlockBlob"
    assert args["validate_content"] is True and args["length"] == len(data) and args["max_concurrency"] == 1
    assert args["content_settings"].cache_control == "private, no-store"
    assert args["metadata"] == {"sha256": digest}
    assert await backend.head("tenant-key") == ObjectMetadata(len(data), digest)
    assert b"".join([chunk async for chunk in backend.read("tenant-key", 2, 4)]) == data[2:6]
    blob.download_blob.assert_awaited_once_with(offset=2, length=4, max_concurrency=1)
    await backend.delete("tenant-key")
    exists = ResourceExistsError("exists")
    exists.error_code = "BlobAlreadyExists"
    blob.upload_blob.side_effect = exists
    assert not await backend.put("tenant-key", BytesIO(data), ObjectMetadata(len(data), digest))
    exists.error_code = "OtherConflict"
    with pytest.raises(ObjectUnavailable):
        await backend.put("tenant-key", BytesIO(data), ObjectMetadata(len(data), digest))
    blob.get_blob_properties.side_effect = missing("BlobNotFound")
    assert await backend.head("tenant-key") is None
    blob.delete_blob.side_effect = missing("BlobNotFound")
    await backend.delete("tenant-key")
    blob.get_blob_properties.side_effect = missing("ContainerNotFound")
    blob.delete_blob.side_effect = missing("ContainerNotFound")
    with pytest.raises(ObjectUnavailable):
        await backend.head("tenant-key")
    with pytest.raises(ObjectUnavailable):
        await backend.delete("tenant-key")
    download.size = 8
    with pytest.raises(ObjectUnavailable):
        _ = [chunk async for chunk in backend.read("tenant-key", 2, 4)]
    for name in ("upload_blob", "get_blob_properties", "download_blob", "delete_blob"):
        getattr(blob, name).side_effect = RuntimeError("private-provider-details")
    for call in [lambda: backend.put("key", BytesIO(data), ObjectMetadata(len(data), digest)), lambda: backend.head("key"), lambda: backend.delete("key")]:
        with pytest.raises(ObjectUnavailable) as exc:
            await call()
        assert "private-provider-details" not in str(exc.value)
    with pytest.raises(ObjectUnavailable):
        _ = [chunk async for chunk in backend.read("key", 0, 1)]


class Container:
    url = "https://unit.blob.core.windows.net/private-container"
    def __init__(self, policy):
        self.get_container_access_policy = AsyncMock(return_value=policy)
        self.closed = False
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        self.closed = True


@pytest.mark.asyncio
@pytest.mark.parametrize("policy,allowed", [({"public_access": None}, True), ({"public_access": "blob"}, False), ({"public_access": "container"}, False), ({}, False), (None, False)])
async def test_azure_private_policy_is_required_and_client_always_closes(monkeypatch, policy, allowed):
    client = Container(policy)
    factory = Mock(return_value=client)
    monkeypatch.setattr(endpoint.ContainerClient, "from_connection_string", factory)
    selected = SimpleNamespace(azure_connection_string="unit-only-connection", azure_container_name="private-container")
    if allowed:
        async with endpoint.azure_store(selected) as backend:
            assert backend.container is client and not client.closed
    else:
        with pytest.raises(HTTPException) as exc:
            async with endpoint.azure_store(selected):
                pytest.fail("public or unknown policy accepted")
        assert exc.value.status_code == 503
    assert client.closed
    assert factory.call_args.kwargs["max_single_put_size"] == MAX_OBJECT_BYTES
    assert factory.call_args.kwargs["max_single_get_size"] == 65536
    assert factory.call_args.kwargs["permit_redirects"] is False


@pytest.mark.asyncio
async def test_azure_config_failure_http_endpoint_and_lookup_errors_deny(monkeypatch):
    client = Container({"public_access": None})
    factory = Mock(return_value=client)
    monkeypatch.setattr(endpoint.ContainerClient, "from_connection_string", factory)
    for config in ({}, {"azure_connection_string": "", "azure_container_name": "valid"}, {"azure_connection_string": "unit", "azure_container_name": "../escape"}):
        with pytest.raises(HTTPException):
            async with endpoint.azure_store(SimpleNamespace(**config)):
                pytest.fail("invalid config accepted")
    factory.assert_not_called()
    selected = SimpleNamespace(azure_connection_string="unit", azure_container_name="valid")
    client.url = "http://unit.blob.core.windows.net/valid"
    with pytest.raises(HTTPException):
        async with endpoint.azure_store(selected):
            pytest.fail("HTTP accepted")
    client.get_container_access_policy.assert_not_awaited()
    assert client.closed
    client.url = "https://unit.blob.core.windows.net/valid"
    client.get_container_access_policy.side_effect = RuntimeError("private-details")
    with pytest.raises(HTTPException) as exc:
        async with endpoint.azure_store(selected):
            pytest.fail("failed policy accepted")
    assert "private-details" not in str(exc.value)
    factory.side_effect = ValueError("private-connection")
    with pytest.raises(HTTPException) as exc:
        async with endpoint.azure_store(selected):
            pytest.fail("failed construction accepted")
    assert "private-connection" not in str(exc.value)


@pytest.mark.asyncio
async def test_azure_cancelled_write_does_not_keep_using_the_upload_body():
    body = BytesIO(b"abc")
    stopped = False
    async def slow(*args, **kwargs):
        nonlocal stopped
        try:
            assert args[0].read() == b"abc"
            await anyio.sleep_forever()
        finally:
            stopped = True
            assert not body.closed
    backend = AzureTenantObjects(SimpleNamespace(get_blob_client=lambda key: SimpleNamespace(upload_blob=slow)))
    with pytest.raises(TimeoutError):
        with anyio.fail_after(0.01):
            await backend.put("key", body, ObjectMetadata(3, hashlib.sha256(b"abc").hexdigest()))
    body.close()
    assert stopped


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "https://attacker.example/private-container", "https://127.0.0.1/private-container",
    "https://unit.blob.core.windows.net.attacker.example/private-container",
    "https://unit.blob.core.windows.net:8443/private-container",
    "https://unit.blob.core.windows.net/other-container",
    "https://unit.blob.core.windows.net/private-container?redirect=internal",
])
async def test_unapproved_azure_endpoint_rejected_before_policy_request(monkeypatch,url):
    client=Container({"public_access":None})
    client.url=url
    monkeypatch.setattr(endpoint.ContainerClient,"from_connection_string",Mock(return_value=client))
    selected=SimpleNamespace(azure_connection_string="unit",azure_container_name="private-container")
    with pytest.raises(HTTPException) as exc:
        async with endpoint.azure_store(selected):
            pytest.fail("Unapproved endpoint accepted")
    assert exc.value.status_code==503 and client.closed
    client.get_container_access_policy.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('operation', ['policy','head','read','put','delete'])
async def test_real_sdk_pipeline_never_follows_private_redirects(monkeypatch,operation):
    import base64
    from azure.core.pipeline.transport import AsyncHttpResponse, AsyncHttpTransport
    from azure.storage.blob.aio import ContainerClient
    class Reply(AsyncHttpResponse):
        def __init__(self,request,status):
            super().__init__(request,None)
            self.status_code=status
            self.headers={'location':'http://169.254.169.254/private','content-type':'application/xml'}
            self.reason='Redirect' if status==307 else 'OK'
            self.content_type='application/xml'
        def body(self):
            return b'<SignedIdentifiers />' if self.status_code==200 else b''
    class Transport(AsyncHttpTransport):
        def __init__(self):
            self.urls=[]
        async def open(self): pass
        async def close(self): pass
        async def __aenter__(self): return self
        async def __aexit__(self,*args): await self.close()
        async def send(self,request,**kwargs):
            self.urls.append(request.url)
            assert request.url.startswith('https://unit.blob.core.windows.net/')
            return Reply(request,200 if operation!='policy' and len(self.urls)==1 else 307)
    transport=Transport()
    factory=ContainerClient.from_connection_string
    monkeypatch.setattr(endpoint.ContainerClient,'from_connection_string',lambda *args,**kwargs: factory(*args,transport=transport,**kwargs))
    selected=SimpleNamespace(azure_container_name='private-container',azure_connection_string=
        'DefaultEndpointsProtocol=https;AccountName=unit;AccountKey='+base64.b64encode(b'a'*64).decode()+';EndpointSuffix=core.windows.net')
    with pytest.raises(HTTPException) as exc:
        async with endpoint.azure_store(selected) as backend:
            if operation=='head': await backend.head('key')
            elif operation=='read': _=[chunk async for chunk in backend.read('key',0,3)]
            elif operation=='put': await backend.put('key',BytesIO(b'abc'),ObjectMetadata(3,hashlib.sha256(b'abc').hexdigest()))
            elif operation=='delete': await backend.delete('key')
    assert exc.value.status_code==503
    assert len(transport.urls)==(1 if operation=='policy' else 2)
