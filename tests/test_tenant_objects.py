"""Object protocol unit tests with memory and SDK doubles only."""
import base64
import hashlib
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from botocore.exceptions import ClientError
from fastapi import HTTPException, Request

from api.domain.tenant_objects import MAX_OBJECT_BYTES, ObjectInputError, ObjectMetadata, ObjectUnavailable, TenantObjects, byte_range, object_key
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects


async def chunks(*parts):
    for part in parts:
        yield part


class Memory:
    def __init__(self):
        self.rows = {}
        self.puts = 0

    async def put(self, key, body, metadata):
        self.puts += 1
        if key in self.rows:
            return False
        self.rows[key] = (body.read(), metadata)
        return True

    async def head(self, key):
        return self.rows[key][1] if key in self.rows else None

    async def read(self, key, start, length):
        yield self.rows[key][0][start:start + length]

    async def delete(self, key):
        self.rows.pop(key, None)


@pytest.mark.asyncio
async def test_tenant_isolation_idempotency_range_and_delete():
    backend = Memory()
    a, b = TenantObjects(uuid4(), backend), TenantObjects(uuid4(), backend)
    data = b"opaque-client-ciphertext"
    object_id = hashlib.sha256(data).hexdigest()
    assert await a.put(object_id, len(data), chunks(data[:3], data[3:])) is True
    assert await a.put(object_id, len(data), chunks(data)) is False
    assert await a.head(object_id) == ObjectMetadata(len(data), object_id)
    assert await b.head(object_id) is None
    assert b"".join([part async for part in a.read(object_id, 2, 4)]) == data[2:6]
    assert await b.put(object_id, len(data), chunks(data)) is True
    await a.delete(object_id)
    await a.delete(object_id)
    assert await a.head(object_id) is None and await b.head(object_id) is not None
    assert all(str(a.tenant) not in key for key in backend.rows)


@pytest.mark.asyncio
@pytest.mark.parametrize("size,parts", [(3, [b"ab"]), (1, [b"ab"]), (2, [b"cd"]), (0, []), (MAX_OBJECT_BYTES + 1, []), (True, [b"a"])])
async def test_invalid_uploads_never_reach_provider(size, parts):
    backend = Memory()
    store = TenantObjects(uuid4(), backend)
    with pytest.raises(ObjectInputError):
        await store.put(hashlib.sha256(b"ab").hexdigest(), size, chunks(*parts))
    assert backend.puts == 0


@pytest.mark.asyncio
async def test_interrupted_upload_and_untrusted_provider_results_fail_closed():
    backend = Memory()
    store = TenantObjects(uuid4(), backend)
    object_id = hashlib.sha256(b"abc").hexdigest()
    async def broken():
        yield b"a"
        raise RuntimeError("disconnect")
    with pytest.raises(RuntimeError, match="disconnect"):
        await store.put(object_id, 3, broken())
    assert backend.puts == 0
    backend.rows[object_key(store.tenant, object_id)] = (b"ab", ObjectMetadata(2, "f" * 64))
    with pytest.raises(ObjectUnavailable):
        await store.head(object_id)
    with pytest.raises(ObjectUnavailable):
        await store.put(object_id, 3, chunks(b"abc"))
    with pytest.raises(ObjectUnavailable, match="Truncated"):
        _ = [part async for part in store.read(object_id, 0, 3)]


@pytest.mark.parametrize("value,expected", [(None, (0, 10)), ("bytes=0-0", (0, 1)), ("bytes=3-", (3, 7)),
    ("bytes=-3", (7, 3)), ("bytes=-20", (0, 10)), ("bytes=7-99", (7, 3))])
def test_single_ranges(value, expected):
    assert byte_range(value, 10) == expected


@pytest.mark.parametrize("value", ["bytes=", "bytes=-", "bytes=-0", "bytes=10-", "bytes=5-3", "bytes=0-1,4-5", "items=0-1", "bytes=" + "1" * 90 + "-"])
def test_invalid_ranges(value):
    with pytest.raises(ObjectInputError):
        byte_range(value, 10)


@pytest.mark.parametrize("value", ["../secret", "A" * 64, "f" * 63, None])
def test_object_ids_are_not_paths(value):
    with pytest.raises(ObjectInputError):
        object_key(uuid4(), value)


@pytest.mark.asyncio
async def test_s3_write_binds_checksum_and_is_conditional_without_public_acl():
    data = b"ciphertext"
    digest = hashlib.sha256(data).hexdigest()
    client = SimpleNamespace(put_object=Mock(), head_object=Mock(return_value={"ContentLength": len(data), "Metadata": {"sha256": digest}}))
    backend = S3TenantObjects(client, "private-bucket")
    assert await backend.put("tenant-key", BytesIO(data), ObjectMetadata(len(data), digest)) is True
    kwargs = client.put_object.call_args.kwargs
    assert kwargs["IfNoneMatch"] == "*" and "ACL" not in kwargs
    assert kwargs["ChecksumSHA256"] == base64.b64encode(hashlib.sha256(data).digest()).decode()
    assert kwargs["ContentType"] == "application/octet-stream" and kwargs["CacheControl"] == "private, no-store"
    assert await backend.head("tenant-key") == ObjectMetadata(len(data), digest)
    client.put_object.side_effect = ClientError({"ResponseMetadata": {"HTTPStatusCode": 412}, "Error": {"Code": "PreconditionFailed"}}, "PutObject")
    assert await backend.put("tenant-key", BytesIO(data), ObjectMetadata(len(data), digest)) is False
    client.put_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "PutObject")
    with pytest.raises(ObjectUnavailable):
        await backend.put("tenant-key", BytesIO(data), ObjectMetadata(len(data), digest))


@pytest.mark.asyncio
async def test_s3_reads_close_stream_and_reject_ignored_range():
    stream = BytesIO(b"abc")
    client = SimpleNamespace(get_object=Mock(return_value={"Body": stream, "ContentLength": 3, "ContentRange": "bytes 2-4/10"}))
    backend = S3TenantObjects(client, "private-bucket")
    assert b"".join([part async for part in backend.read("key", 2, 3)]) == b"abc"
    assert stream.closed and client.get_object.call_args.kwargs["Range"] == "bytes=2-4"
    stream = BytesIO(b"too-many")
    client.get_object.return_value = {"Body": stream, "ContentLength": 8}
    with pytest.raises(ObjectUnavailable):
        _ = [part async for part in backend.read("key", 2, 3)]
    assert stream.closed


@pytest.mark.asyncio
async def test_http_handlers_bound_upload_and_do_not_expose_provider_paths():
    from api.interfaces.api_controllers import tenant_objects_endpoint as endpoint
    backend = Memory()
    store = TenantObjects(uuid4(), backend)
    data = b"ciphertext"
    object_id = hashlib.sha256(data).hexdigest()
    def request(method, headers=(), body=data):
        sent = False
        async def receive():
            nonlocal sent
            if sent:
                return {"type": "http.disconnect"}
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return Request({"type": "http", "method": method, "path": "/", "headers": list(headers)}, receive)
    upload = request("PUT", [(b"content-type", b"application/octet-stream"), (b"content-length", str(len(data)).encode())])
    assert (await endpoint.put_object(object_id, upload, store)).status_code == 201
    head = await endpoint.get_object(object_id, request("HEAD"), store)
    assert head.headers["content-length"] == str(len(data)) and head.headers["etag"] == f'"{object_id}"'
    get = await endpoint.get_object(object_id, request("GET", [(b"range", b"bytes=2-4")]), store)
    assert get.status_code == 206 and get.headers["content-range"] == f"bytes 2-4/{len(data)}"
    assert b"".join([part async for part in get.body_iterator]) == data[2:5]
    with pytest.raises(HTTPException) as exc:
        await endpoint.put_object(object_id, request("PUT", [(b"content-type", b"text/html")]), store)
    assert exc.value.status_code == 415
    assert (await endpoint.delete_object(object_id, store)).status_code == 204
    with pytest.raises(HTTPException) as exc:
        await endpoint.get_object(object_id, request("GET"), store)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_s3_missing_is_distinct_from_permission_failure_and_delete_failure():
    client = SimpleNamespace(head_object=Mock(), delete_object=Mock())
    backend = S3TenantObjects(client, "private-bucket")
    with pytest.raises(ObjectUnavailable):
        S3TenantObjects(client, "bad/bucket")
    client.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    assert await backend.head("key") is None
    client.head_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "HeadObject")
    with pytest.raises(ObjectUnavailable):
        await backend.head("key")
    await backend.delete("key")
    client.delete_object.assert_called_once_with(Bucket="private-bucket", Key="key")
    client.delete_object.side_effect = ClientError({"Error": {"Code": "AccessDenied"}}, "DeleteObject")
    with pytest.raises(ObjectUnavailable):
        await backend.delete("key")


@pytest.mark.asyncio
async def test_http_errors_never_expose_backend_exception_details():
    from api.interfaces.api_controllers import tenant_objects_endpoint as endpoint
    backend = Memory()
    store = TenantObjects(uuid4(), backend)
    object_id = hashlib.sha256(b"abc").hexdigest()
    def request(method, headers=(), body=b"abc"):
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        return Request({"type": "http", "method": method, "path": "/", "headers": list(headers)}, receive)
    binary = [(b"content-type", b"application/octet-stream")]
    for extra, status in [([], 411), ([(b"content-length", b"999999999")], 413), ([(b"content-length", b"2")], 400)]:
        with pytest.raises(HTTPException) as exc:
            await endpoint.put_object(object_id, request("PUT", binary + extra), store)
        assert exc.value.status_code == status
    await store.put(object_id, 3, chunks(b"abc"))
    with pytest.raises(HTTPException) as exc:
        await endpoint.get_object(object_id, request("GET", [(b"range", b"bytes=9-")]), store)
    assert exc.value.status_code == 416 and exc.value.headers["Content-Range"] == "bytes */3"
    full = await endpoint.get_object(object_id, request("GET", [(b"range", b"bytes=1-2"), (b"if-range", b'"different"')]), store)
    assert full.status_code == 200
    assert b"".join([part async for part in full.body_iterator]) == b"abc"
    for operation in [lambda: endpoint.get_object("../bad", request("GET"), store), lambda: endpoint.delete_object("../bad", store)]:
        with pytest.raises(HTTPException) as exc:
            await operation()
        assert exc.value.status_code == 400
    failing = SimpleNamespace(put=AsyncMock(side_effect=RuntimeError("private-provider-details")),
        head=AsyncMock(side_effect=RuntimeError("private-provider-details")), delete=AsyncMock(side_effect=RuntimeError("private-provider-details")))
    for operation in [lambda: endpoint.put_object(object_id, request("PUT", binary + [(b"content-length", b"3")]), failing),
        lambda: endpoint.get_object(object_id, request("GET"), failing), lambda: endpoint.delete_object(object_id, failing)]:
        with pytest.raises(HTTPException) as exc:
            await operation()
        assert exc.value.status_code == 503 and "private-provider-details" not in exc.value.detail
