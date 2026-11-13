"""SDK pagination and worker transaction contracts; no provider connections."""
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest

from api import experiment_retention as worker
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects


@pytest.mark.asyncio
async def test_azure_listing_uses_exact_prefix_and_resumes_provider_cursor():
    class Pages:
        continuation_token = "next"
        async def __anext__(self):
            async def rows():
                yield SimpleNamespace(name="prefix/name")
            return rows()
    pages = Pages()
    by_page = Mock(return_value=pages)
    container = SimpleNamespace(list_blobs=Mock(return_value=SimpleNamespace(by_page=by_page)))
    _records, page = worker.listing(AzureTenantObjects(container))
    assert await page("prefix/", "before") == (["prefix/name"], "next")
    container.list_blobs.assert_called_once_with(name_starts_with="prefix/", results_per_page=1000)
    by_page.assert_called_once_with(continuation_token="before")


@pytest.mark.asyncio
async def test_s3_listing_requires_a_continuation_for_truncated_results():
    client = SimpleNamespace(list_objects_v2=Mock(return_value={"Contents": [{"Key": "prefix/name"}], "IsTruncated": True, "NextContinuationToken": "next"}))
    _records, page = worker.listing(S3TenantObjects(client, "private-bucket"))
    assert await page("prefix/", "before") == (["prefix/name"], "next")
    client.list_objects_v2.assert_called_once_with(Bucket="private-bucket", Prefix="prefix/", MaxKeys=1000, ContinuationToken="before")
    client.list_objects_v2.return_value = {"IsTruncated": True}
    with pytest.raises(ValueError):
        await page("prefix/", None)
    client.list_objects_v2.return_value = {"IsTruncated": False}
    assert await page("prefix/", None) == ([], None)


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_worker_commits_or_rolls_back_and_always_resets_tenant_context(monkeypatch, failed):
    tenant = UUID("12345678-1234-4234-8234-123456789abc")
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    @asynccontextmanager
    async def maker():
        yield session
    @asynccontextmanager
    async def provider(selected):
        assert selected == tenant
        yield "objects"
    set_context, reset = Mock(return_value="context"), Mock()
    guc = AsyncMock()
    sweep = AsyncMock(return_value={"scanned": 1, "removed": 1, "more": False})
    if failed:
        sweep.side_effect = RuntimeError("provider private value")
    monkeypatch.setattr(worker, "set_db_session", set_context)
    monkeypatch.setattr(worker, "reset_db_session", reset)
    monkeypatch.setattr(worker, "set_current_tenant_id_guc", guc)
    monkeypatch.setattr(worker, "tenant_backend", provider)
    monkeypatch.setattr(worker, "listing", lambda _: ("records", "page"))
    monkeypatch.setattr(worker, "sweep_page", sweep)
    if failed:
        with pytest.raises(RuntimeError):
            await worker.tick(maker, tenant, "hypermesh", 1000)
        session.rollback.assert_awaited_once()
        session.commit.assert_not_awaited()
    else:
        assert (await worker.tick(maker, tenant, "hypermesh", 1000))["removed"] == 1
        session.commit.assert_awaited_once()
        session.rollback.assert_not_awaited()
    guc.assert_awaited_once_with(session, tenant)
    reset.assert_called_once_with("context")


@pytest.mark.asyncio
async def test_http_outbox_limit_refuses_body_before_consumption():
    from fastapi import Request, HTTPException
    from api.interfaces.api_controllers.tenant_records_endpoint import put_record
    async def receive():
        pytest.fail("Oversized outbox must not be read")
    request = Request({"type": "http", "method": "PUT", "path": "/", "headers": [
        (b"content-type", b"application/octet-stream"), (b"content-length", b"65537"), (b"if-none-match", b"*")]}, receive)
    store = SimpleNamespace(put=AsyncMock())
    with pytest.raises(HTTPException) as error:
        await put_record("experiments", "hypermesh-outbox-" + "a" * 64, request, store)
    assert error.value.status_code == 413
    store.put.assert_not_awaited()
