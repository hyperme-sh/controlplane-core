"""Tenant-owner HTTP configuration contracts with an in-memory conditional provider."""
from uuid import UUID

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from api.domain.experiments.store import ExperimentStore
from api.infrastructure.security.storage_admin import StorageAdmin
from api.interfaces.api_controllers import experiment_admin_endpoint as endpoint
from test_experiment_store import Backend, config

TENANT = UUID(int=1)
PATH = "/storage/tenant/experiments/s3r.ch"


@pytest.fixture
def setup():
    app = FastAPI()
    app.include_router(endpoint.router, prefix="/storage/tenant/experiments")
    backend = Backend()
    store = ExperimentStore(TENANT, backend)
    app.dependency_overrides[endpoint.storage_admin] = lambda: StorageAdmin(TENANT, "owner")
    app.dependency_overrides[endpoint.configured_store] = lambda: store
    return app, backend, store


def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://unit")


@pytest.mark.asyncio
async def test_admin_create_read_replace_and_stale_update(setup):
    app, backend, _ = setup
    async with client(app) as c:
        assert (await c.get(PATH)).status_code == 404
        created = await c.put(PATH, content=config().model_dump_json(),
            headers={"content-type": "application/json", "if-none-match": "*"})
        assert created.status_code == 201 and created.headers["etag"] == '"1"'
        assert created.headers["cache-control"] == "private, no-store"
        assert (await c.get(PATH)).json() == config().model_dump(mode="json")
        updated = await c.put(PATH, content=config(2).model_dump_json(),
            headers={"content-type": "application/json", "if-match": '"1"'})
        assert updated.status_code == 200 and updated.headers["etag"] == '"2"'
        stale = await c.put(PATH, content=config(2).model_dump_json(),
            headers={"content-type": "application/json", "if-match": '"1"'})
        assert stale.status_code == 412 and backend.count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"if-match": "*"}, {"if-match": '"0"'},
    {"if-match": '"9007199254740992"'}, {"if-match": '"1"', "if-none-match": "*"}])
async def test_mutations_require_one_exact_precondition(setup, headers):
    app, backend, _ = setup
    async with client(app) as c:
        result = await c.put(PATH, content=config().model_dump_json(), headers={"content-type": "application/json", **headers})
    assert result.status_code == 428 and backend.count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("body,status", [(config(tenant=UUID(int=2)).model_dump_json(), 403),
    (config(audience="portal").model_dump_json(), 403), (config(2).model_dump_json(), 409),
    ('{"private":"do-not-echo"}', 400), ('x'*32769, 413)])
async def test_wrong_scope_shape_sequence_and_oversize_refuse_without_writes(setup, body, status):
    app, backend, _ = setup
    async with client(app) as c:
        result = await c.put(PATH, content=body, headers={"content-type": "application/json", "if-none-match": "*"})
    assert result.status_code == status and backend.count == 0
    assert "do-not-echo" not in result.text


@pytest.mark.asyncio
async def test_revoked_admin_cannot_read_or_mutate_or_initialize_backend(setup):
    app, backend, _ = setup
    async def denied():
        raise HTTPException(403, "Not allowed")
    def unused():
        raise AssertionError("unauthorized backend initialization")
    app.dependency_overrides[endpoint.storage_admin] = denied
    app.dependency_overrides[endpoint.configured_store] = unused
    async with client(app) as c:
        assert (await c.get(PATH)).status_code == 403
        assert (await c.put(PATH, content=config().model_dump_json(), headers={"if-none-match": "*"})).status_code == 403
    assert backend.count == 0


@pytest.mark.asyncio
async def test_provider_failure_and_wrong_content_type_are_private(setup):
    app, backend, store = setup
    async with client(app) as c:
        result = await c.put(PATH, content="{}", headers={"if-none-match": "*", "content-type": "text/plain"})
        assert result.status_code == 415
        async def failed(*_):
            raise RuntimeError("private-provider-token")
        backend.fetch = failed
        result = await c.get(PATH)
        assert result.status_code == 503 and "private-provider-token" not in result.text


@pytest.mark.asyncio
async def test_chunked_oversize_body_is_bounded(setup):
    app, backend, _ = setup
    async def chunks():
        yield b"x" * 16384
        yield b"x" * 16385
    async with client(app) as c:
        result = await c.put(PATH, content=chunks(), headers={
            "content-type": "application/json", "if-none-match": "*"})
    assert result.status_code == 413 and backend.count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,status", [
    ([("content-length", "bad")], 400),
    ([("content-length", "1"), ("content-length", "2")], 400),
    ([("if-none-match", "*")], 428),
    ([("content-encoding", "gzip")], 415),
])
async def test_ambiguous_headers_refuse(setup, extra, status):
    app, backend, _ = setup
    async with client(app) as c:
        result = await c.put(PATH, content=b"{}", headers=[
            ("content-type", "application/json"), ("if-none-match", "*"), *extra])
    assert result.status_code == status and backend.count == 0


@pytest.mark.asyncio
async def test_provider_race_and_duplicate_create_do_not_overwrite(setup):
    app, backend, store = setup
    await store.initialize(config())
    async with client(app) as c:
        duplicate = await c.put(PATH, content=config().model_dump_json(), headers={
            "content-type": "application/json", "if-none-match": "*"})
        assert duplicate.status_code == 412
        backend.conflict = True
        race = await c.put(PATH, content=config(2).model_dump_json(), headers={
            "content-type": "application/json", "if-match": '\"1\"'})
        assert race.status_code == 412
    assert backend.count == 1
