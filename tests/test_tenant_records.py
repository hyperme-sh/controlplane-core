"""Conditional record unit contracts with memory, HTTP, and SDK doubles."""
import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from fastapi import FastAPI, Request, HTTPException
import httpx
import pytest

from api.domain.tenant_objects import ObjectInputError, ObjectUnavailable
from api.domain.tenant_records import MAGIC, MAX_RECORD_BYTES, RecordConflict, TenantRecords, decode, record_key
from api.infrastructure.externals.tenant_record_azure import AzureTenantRecords
from api.infrastructure.externals.tenant_record_s3 import S3TenantRecords
from api.interfaces.api_controllers import tenant_records_endpoint as route


class Memory:
    def __init__(self):
        self.rows = {}
        self.barrier = None
        self.reads = 0

    async def fetch(self, key):
        result = self.rows.get(key)
        if self.barrier:
            self.reads += 1
            if self.reads == 2:
                self.barrier.set()
            await self.barrier.wait()
        return result

    async def replace(self, key, data, etag):
        current = self.rows.get(key)
        if (current[1] if current else None) != etag:
            raise RecordConflict()
        self.rows[key] = (data, '"' + hashlib.md5(data).hexdigest() + '"')

    async def remove(self, key, etag):
        if key not in self.rows or self.rows[key][1] != etag:
            raise RecordConflict()
        del self.rows[key]


@pytest.mark.asyncio
async def test_create_update_delete_aba_and_tenant_namespace_isolation():
    backend = Memory()
    owner, other = TenantRecords(uuid4(), backend), TenantRecords(uuid4(), backend)
    first = await owner.put("cache", "feed", b"public", None)
    assert (await owner.get("cache", "feed")) == first
    assert await owner.get("fixtures", "feed") is None
    assert await other.get("cache", "feed") is None
    with pytest.raises(RecordConflict):
        await owner.put("cache", "feed", b"lost update", None)
    second = await owner.put("cache", "feed", b"public", first.revision)
    assert second.data == first.data and second.revision != first.revision
    with pytest.raises(RecordConflict):
        await owner.put("cache", "feed", b"stale", first.revision)
    with pytest.raises(RecordConflict):
        await owner.delete("cache", "feed", first.revision)
    await owner.delete("cache", "feed", second.revision)
    recreated = await owner.put("cache", "feed", b"public", None)
    assert recreated.revision not in (first.revision, second.revision)
    with pytest.raises(RecordConflict):
        await owner.delete("cache", "feed", second.revision)


@pytest.mark.asyncio
async def test_two_writers_reading_the_same_revision_cannot_both_commit():
    backend = Memory()
    store = TenantRecords(uuid4(), backend)
    first = await store.put("cache", "feed", b"before", None)
    backend.barrier = asyncio.Event()
    results = await asyncio.gather(*(store.put("cache", "feed", value, first.revision)
        for value in (b"writer-one", b"writer-two")), return_exceptions=True)
    assert sum(isinstance(result, RecordConflict) for result in results) == 1


@pytest.mark.parametrize("namespace,name", [("secret", "x"), ("cache", "../x"), ("cache", "a/b"), ("cache", ""), ("cache", "x"*129)])
def test_keys_cannot_cross_namespaces(namespace, name):
    with pytest.raises(ObjectInputError):
        record_key(uuid4(), namespace, name)


@pytest.mark.asyncio
async def test_invalid_or_corrupt_records_never_overwrite_data():
    backend = Memory()
    store = TenantRecords(uuid4(), backend)
    for value, expected in ((b"", None), (b"a"*(MAX_RECORD_BYTES+1), None), (b"a", "*")):
        with pytest.raises(ObjectInputError):
            await store.put("cache", "feed", value, expected)
    for value in (b"not-a-record", MAGIC + b"a"*32, MAGIC+b"a"*(MAX_RECORD_BYTES+33)):
        with pytest.raises(ObjectUnavailable):
            decode(value)
    with pytest.raises(RecordConflict):
        await store.put("cache", "feed", b"a", "a"*64)
    with pytest.raises(RecordConflict):
        await store.delete("cache", "feed", "a"*64)
    assert backend.rows == {}


@pytest.mark.asyncio
async def test_http_requires_conditional_mutations_and_preserves_opaque_body():
    app = FastAPI()
    app.include_router(route.router, prefix="/api/v1")
    store = TenantRecords(uuid4(), Memory())
    app.dependency_overrides[route.records_store] = lambda: store
    path = "/api/v1/storage/records/cache/public-feed"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://storage") as client:
        assert (await client.get(path)).status_code == 404
        assert (await client.put(path, content=b"snapshot")).status_code == 428
        base = {"Content-Type": "application/octet-stream"}
        created = await client.put(path, content=b"snapshot", headers={**base, "If-None-Match": "*"})
        assert created.status_code == 201
        first = created.headers["etag"]
        read = await client.get(path)
        assert read.content == b"snapshot" and read.headers["etag"] == first
        assert read.headers["cache-control"] == "private, no-store"
        assert (await client.put(path, content=b"stale", headers={**base, "If-None-Match": "*"})).status_code == 412
        updated = await client.put(path, content=b"new", headers={**base, "If-Match": first})
        assert updated.status_code == 204 and updated.headers["etag"] != first
        assert (await client.delete(path, headers={"If-Match": first})).status_code == 412
        assert (await client.delete(path, headers={"If-Match": updated.headers["etag"]})).status_code == 204
        for headers in ({"If-Match": "*"}, {"If-None-Match": "*"},
                {"If-Match": first, "If-None-Match": "*"}):
            assert (await client.delete(path, headers=headers)).status_code == 428
        assert (await client.put(path, content=b"bad", headers={"If-None-Match": "*", "Content-Type": "text/plain"})).status_code == 415
        assert (await client.put(path, content=b"", headers={**base, "If-None-Match": "*"})).status_code == 413


async def chunks(*items):
    for item in items:
        yield item


@pytest.mark.asyncio
async def test_azure_read_and_all_mutations_are_conditional():
    blob = SimpleNamespace(get_blob_properties=AsyncMock(return_value=SimpleNamespace(size=3, etag='"old"')),
        download_blob=AsyncMock(return_value=SimpleNamespace(chunks=lambda: chunks(b"a", b"bc"))),
        upload_blob=AsyncMock(), delete_blob=AsyncMock())
    backend = AzureTenantRecords(SimpleNamespace(get_blob_client=lambda _: blob))
    assert await backend.fetch("key") == (b"abc", '"old"')
    assert blob.download_blob.call_args.kwargs["match_condition"] == MatchConditions.IfNotModified
    await backend.replace("key", b"abc", None)
    assert blob.upload_blob.call_args.kwargs["overwrite"] is False
    await backend.replace("key", b"abc", '"old"')
    assert blob.upload_blob.call_args.kwargs["etag"] == '"old"'
    assert blob.upload_blob.call_args.kwargs["match_condition"] == MatchConditions.IfNotModified
    await backend.remove("key", '"old"')
    assert blob.delete_blob.call_args.kwargs == {"etag": '"old"', "match_condition": MatchConditions.IfNotModified}
    blob.upload_blob.side_effect = ResourceModifiedError("private provider details")
    with pytest.raises(RecordConflict):
        await backend.replace("key", b"abc", '"old"')
    blob.download_blob.return_value = SimpleNamespace(chunks=lambda: chunks(b"too large"))
    with pytest.raises(ObjectUnavailable):
        await backend.fetch("key")


@pytest.mark.asyncio
async def test_s3_atomic_preconditions_and_bounded_reads(monkeypatch):
    seen = []
    objects = SimpleNamespace(_signed=lambda method, key, headers: ("https://private.example/"+key, headers))
    backend = S3TenantRecords(objects)
    real = httpx.AsyncClient
    def handle(request):
        seen.append(request)
        if request.method == "GET":
            return httpx.Response(200, headers={"ETag": '"old"', "Content-Length": "3"}, stream=httpx.ByteStream(b"abc"))
        return httpx.Response(200 if request.method == "PUT" else 204)
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=httpx.MockTransport(handle), **kwargs))
    assert await backend.fetch("key") == (b"abc", '"old"')
    await backend.replace("key", b"abc", None)
    assert seen[-1].headers["if-none-match"] == "*"
    await backend.replace("key", b"abc", '"old"')
    assert seen[-1].headers["if-match"] == '"old"'
    await backend.remove("key", '"old"')
    assert seen[-1].headers["if-match"] == '"old"'
    assert seen[-1].method == "DELETE"


@pytest.mark.parametrize("suffix", ["", "/access"])
def test_record_and_media_scopes_are_not_interchangeable(monkeypatch, suffix):
    from api.infrastructure.security import storage_session as sessions
    from fastapi import HTTPException
    tenant = uuid4()
    claims = {"azp": "controlplane-api-key", "sub": "apikey:"+str(uuid4()), "iat":100, "exp":200,
        "https://spanopticon.dev/tenant_id":str(tenant), "scope":"storage:objects:read"}
    monkeypatch.setattr(sessions, "try_decode_api_key_session_jwt", lambda _: claims)
    operation = sessions.request_operation("GET", "/api/v1/storage/records/cache/feed" + suffix)
    with pytest.raises(HTTPException) as error:
        sessions.storage_session("token", operation, now=150)
    assert error.value.status_code == 403
    claims["scope"] = "storage:records:read"
    assert sessions.storage_session("token", operation, now=150) == tenant
    with pytest.raises(HTTPException):
        sessions.storage_session("token", "read", now=150)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,status,headers,body", [
    ("GET",404,{},b""), ("GET",403,{},b"private error"),
    ("GET",200,{"ETag":'"old"',"Content-Length":"2"},b"abc"),
    ("GET",200,{"ETag":'"old"',"Content-Length":"4"},b"abc"),
    ("GET",200,{"ETag":'"old"',"Content-Length":"0"},b""),
    ("GET",200,{"ETag":'"old"',"Content-Length":"invalid"},b"abc"),
    ("GET",200,{"ETag":'"old"',"Content-Length":"3","Content-Encoding":"gzip"},b"abc"),
    ("GET",200,{"ETag":"bad","Content-Length":"3"},b"abc"),
    ("PUT",412,{},b"private error"), ("PUT",409,{},b"private error"), ("PUT",403,{},b"private error"),
    ("DELETE",404,{},b"private error"), ("DELETE",403,{},b"private error"),
])
async def test_s3_missing_conflicts_and_corrupt_provider_responses(monkeypatch, method, status, headers, body):
    objects = SimpleNamespace(_signed=lambda *args: ("https://private.example/key", {}))
    backend = S3TenantRecords(objects)
    real = httpx.AsyncClient
    transport = httpx.MockTransport(lambda _: httpx.Response(status, headers=headers, stream=httpx.ByteStream(body)))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: real(transport=transport, **kwargs))
    if method=="GET" and status==404:
        assert await backend.fetch("key") is None
    else:
        expected = RecordConflict if status in (409,412) or method=="DELETE" and status==404 else ObjectUnavailable
        with pytest.raises(expected) as error:
            if method=="GET": await backend.fetch("key")
            elif method=="PUT": await backend.replace("key",b"abc",'"old"')
            else: await backend.remove("key",'"old"')
        assert "private error" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("method,kind,code,expected", [
    ("fetch",ResourceNotFoundError,"BlobNotFound",None),
    ("fetch",ResourceNotFoundError,"ContainerNotFound",ObjectUnavailable),
    ("fetch",ResourceModifiedError,"ConditionNotMet",RecordConflict),
    ("fetch",RuntimeError,None,ObjectUnavailable),
    ("replace",ResourceExistsError,"BlobAlreadyExists",RecordConflict),
    ("replace",ResourceExistsError,"ContainerAlreadyExists",ObjectUnavailable),
    ("replace",RuntimeError,None,ObjectUnavailable),
    ("remove",ResourceNotFoundError,"BlobNotFound",RecordConflict),
    ("remove",ResourceNotFoundError,"ContainerNotFound",ObjectUnavailable),
    ("remove",ResourceModifiedError,"ConditionNotMet",RecordConflict),
    ("remove",RuntimeError,None,ObjectUnavailable),
])
async def test_azure_failure_categories_and_private_error_redaction(method,kind,code,expected):
    error=kind("provider-secret")
    error.error_code=code
    blob=SimpleNamespace(get_blob_properties=AsyncMock(side_effect=error),upload_blob=AsyncMock(side_effect=error),delete_blob=AsyncMock(side_effect=error))
    backend=AzureTenantRecords(SimpleNamespace(get_blob_client=lambda _:blob))
    async def invoke():
        if method=="fetch":return await backend.fetch("key")
        if method=="replace":return await backend.replace("key",b"data",None)
        return await backend.remove("key",'"old"')
    if expected is None:
        assert await invoke() is None
    else:
        with pytest.raises(expected) as caught:await invoke()
        assert "provider-secret" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("size,data", [(0,b""),(MAX_RECORD_BYTES+100,b""),(3,b"a")])
async def test_azure_rejects_unbounded_or_short_reads(size,data):
    blob=SimpleNamespace(get_blob_properties=AsyncMock(return_value=SimpleNamespace(size=size,etag='"old"')),
        download_blob=AsyncMock(return_value=SimpleNamespace(chunks=lambda:chunks(data))))
    with pytest.raises(ObjectUnavailable):
        await AzureTenantRecords(SimpleNamespace(get_blob_client=lambda _:blob)).fetch("key")


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [b"ab",b"abcd"])
async def test_http_declared_length_mismatch_never_reaches_store(payload):
    async def receive():return {"type":"http.request","body":payload,"more_body":False}
    request=Request({"type":"http","method":"PUT","path":"/", "headers":[
        (b"if-none-match",b"*"),(b"content-type",b"application/octet-stream"),(b"content-length",b"3")]},receive)
    store=SimpleNamespace(put=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await route.put_record("cache","feed",request,store)
    assert error.value.status_code==400
    store.put.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("error,status",[(ObjectUnavailable("secret"),503),(TimeoutError("secret"),504),(ObjectInputError("secret"),400)])
async def test_http_read_errors_are_sanitized(error,status):
    with pytest.raises(HTTPException) as caught:
        await route.get_record("cache","feed",SimpleNamespace(get=AsyncMock(side_effect=error)))
    assert caught.value.status_code==status and "secret" not in caught.value.detail


def test_records_reuse_only_supported_explicit_private_backend_clients():
    from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
    from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects
    tenant=uuid4()
    azure=route.records_store(SimpleNamespace(tenant=tenant,backend=AzureTenantObjects("container")))
    assert isinstance(azure.backend,AzureTenantRecords) and azure.backend.container=="container"
    s3=route.records_store(SimpleNamespace(tenant=tenant,backend=S3TenantObjects(Mock(),"bucket")))
    assert isinstance(s3.backend,S3TenantRecords)
    with pytest.raises(HTTPException):route.records_store(SimpleNamespace(tenant=tenant,backend=object()))
