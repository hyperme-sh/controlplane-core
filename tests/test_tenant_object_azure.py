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
