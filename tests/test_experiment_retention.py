"""Experiment scope and retention contracts with conditional storage doubles."""
import hashlib
import json
from uuid import UUID

import pytest

from api.domain.experiments.records import validate_write
from api.domain.experiments.retention import sweep_page
from api.domain.tenant_objects import ObjectInputError
from api.domain.tenant_records import MAGIC, RecordConflict, TenantRecords, decode, record_key
from api.infrastructure.security.storage_session import request_operation

TENANT = UUID("12345678-1234-4234-8234-123456789abc")
NAME = "hypermesh-outbox-" + "a" * 64


class Memory:
    def __init__(self):
        self.rows = {}
        self.removed = []
        self.conflict = False

    async def fetch(self, key):
        return self.rows.get(key)

    async def replace(self, key, data, etag):
        old = self.rows.get(key)
        if (old[1] if old else None) != etag:
            raise RecordConflict()
        self.rows[key] = data, '"' + hashlib.sha256(data).hexdigest() + '"'

    async def remove(self, key, etag):
        if self.conflict or self.rows[key][1] != etag:
            raise RecordConflict()
        self.removed.append(key)
        del self.rows[key]


def document(expires=2000, revoked=False):
    return json.dumps({"version": 1, "expires": expires, "revoked": revoked, "rows": []}).encode()


def test_scopes_are_per_audience_and_separate_cache_from_outbox():
    for audience in ("s3rch", "hypermesh", "portal"):
        base = "/api/v1/storage/records/experiments/"
        assert request_operation("GET", base + audience + "-cache") == f"experiments.{audience}.cache.read"
        assert request_operation("PUT", base + audience + "-cache") is None
        assert request_operation("GET", base + audience + "-outbox-" + "a" * 64) == f"experiments.{audience}.outbox.read"
        assert request_operation("PUT", base + audience + "-outbox-" + "a" * 64) == f"experiments.{audience}.outbox.write"
        assert request_operation("DELETE", base + audience + "-outbox-" + "a" * 64) is None
    for name in ("unknown-cache", "hypermesh-outbox-bad", "hypermesh-cache/access", "hypermesh-cache/", "../cache/feed"):
        assert request_operation("GET", "/api/v1/storage/records/experiments/" + name) is None
    assert request_operation("GET", "/api/v1/storage/records/cache/feed") == "records.read"
    assert request_operation("PUT", "/api/v1/storage/records/media/object") == "media.write"


@pytest.mark.asyncio
async def test_cache_alias_preserves_publisher_bytes_but_refuses_mutation():
    backend = Memory()
    records = TenantRecords(TENANT, backend)
    written = await records.put("cache", "experiments-v1-hypermesh", b"signed", None)
    assert await records.get("experiments", "hypermesh-cache") == written
    assert record_key(TENANT, "experiments", "hypermesh-cache") == record_key(TENANT, "cache", "experiments-v1-hypermesh")
    with pytest.raises(ObjectInputError):
        await records.put("experiments", "hypermesh-cache", b"overwrite", written.revision)
    with pytest.raises(ObjectInputError):
        await records.delete("experiments", "hypermesh-cache", written.revision)


def test_retention_cannot_extend_revoked_or_existing_consent():
    validate_write(NAME, document(), None, 1000)
    validate_write(NAME, document(revoked=True), document(), 1000)
    validate_write(NAME, document(revoked=True), document(), 3000)
    for body, previous in [(document(86401001), None), (document(1000), None),
                           (document(3000), document()), (document(), document(revoked=True))]:
        with pytest.raises(ObjectInputError):
            validate_write(NAME, body, previous, 1000)
    for body in [b"null", b"{}", b"x" * 65537, b'{"version":true,"expires":2000,"revoked":false,"rows":[]}']:
        with pytest.raises(ObjectInputError):
            validate_write(NAME, body, None, 1000)


@pytest.mark.asyncio
async def test_paginated_conditional_cleanup_resumes_after_restart():
    backend = Memory()
    first = record_key(TENANT, "experiments", NAME)
    second = record_key(TENANT, "experiments", "hypermesh-outbox-" + "b" * 64)
    await backend.replace(first, MAGIC + b"x" * 32 + document(), None)
    await backend.replace(second, MAGIC + b"x" * 32 + document(4000), None)
    calls = []
    async def page(prefix, token):
        calls.append((prefix, token))
        return ([first], "next") if token is None else ([second], None)
    assert await sweep_page(backend, page, TENANT, "hypermesh", 2000) == {"scanned": 1, "removed": 1, "more": True}
    assert await sweep_page(backend, page, TENANT, "hypermesh", 2000) == {"scanned": 1, "removed": 0, "more": False}
    assert calls[1][1] == "next"
    assert backend.removed == [first]
    assert second in backend.rows


@pytest.mark.asyncio
async def test_scope_mismatch_and_conflicts_never_delete_foreign_or_changed_records():
    backend = Memory()
    key = record_key(TENANT, "experiments", NAME)
    await backend.replace(key, MAGIC + b"x" * 32 + document(), None)
    async def foreign(_prefix, _token):
        return [key.replace("hypermesh-outbox", "portal-outbox")], None
    with pytest.raises(ValueError):
        await sweep_page(backend, foreign, TENANT, "hypermesh", 3000)
    assert backend.removed == []
    backend.conflict = True
    async def page(_prefix, _token):
        return [key], None
    assert (await sweep_page(backend, page, TENANT, "hypermesh", 3000))["removed"] == 0
    assert key in backend.rows


@pytest.mark.asyncio
async def test_malformed_outbox_is_not_blindly_deleted_or_skipped():
    backend = Memory()
    key = record_key(TENANT, "experiments", NAME)
    await backend.replace(key, MAGIC + b"x" * 32 + b"{}", None)
    async def page(_prefix, _token):
        return [key], None
    with pytest.raises(ObjectInputError):
        await sweep_page(backend, page, TENANT, "hypermesh", 3000)
    assert key in backend.rows
    assert len(backend.rows) == 1
