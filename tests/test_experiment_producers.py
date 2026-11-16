"""Private producer contracts with isolated HTTP transports; no provider calls."""
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest

from api.domain.experiments.producers import (
    Admission, Attribution, AzurePublisher, Binding, Measurement, ServerObserver, create_relay,
)

TOKEN = "a" * 64


def binding(**changes):
    return Binding.model_validate_json(json.dumps({"tenant": str(uuid4()), "audience": "hyperme.sh",
        "experiments": {"signup": ["control", "join"]}, "sample_every": 1,
        "operations": {"lookup": {"url": "https://owner.test/lookup", "method": "GET", "kind": "dependency",
                                   "headers": {"authorization": "Bearer owner-only"}}}, **changes}))


def attribution(**changes):
    return Attribution(**dict(dict(action=uuid4(), experiment="signup", variant="join", operation="lookup",
                       issued=int(time.time())), **changes))


def headers(a):
    return {"authorization": "Bearer " + TOKEN, "content-type": "application/octet-stream",
            "x-experiment-attribution": a.model_dump_json()}


@pytest.mark.asyncio
async def test_independent_actual_http_observers_and_ingestion_scope(tmp_path):
    platform, server = [], []
    async def append_platform(m): platform.append(m)
    async def append_server(m): server.append(m)
    async def upstream(request):
        assert request.url == "https://owner.test/lookup"
        assert request.headers["authorization"] == "Bearer owner-only"
        assert "x-experiment-attribution" not in request.headers
        return httpx.Response(503, content=b"provider unavailable")
    admission = Admission(str(tmp_path / "actions.db"))
    b = binding()
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as network:
        relay = create_relay(binding=b, token=TOKEN, admission=admission, client=network, publish=append_platform, enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay), base_url="https://relay.test") as client:
            observer = ServerObserver(binding=b, endpoint="https://relay.test", token=TOKEN, client=client, publish=append_server, enabled=True)
            response = await observer.request(experiment="signup", variant="join", operation="lookup")
    assert response.status_code == 503
    assert response.content == b"provider unavailable"
    assert len(server) == len(platform) == 1
    assert server[0].attribution == platform[0].attribution
    assert server[0].failed and platform[0].failed
    assert server[0].duration_ms >= platform[0].duration_ms >= 0
    assert {row["Metric"] for row in platform[0].rows()} == {"latency_ms", "error_rate", "dependency_failure_rate"}
    assert all(set(row) == {"ActionId", "Experiment", "Variant", "Metric", "ObservedAt", "Value"} for row in platform[0].rows())
    admission.close()


@pytest.mark.asyncio
async def test_disabled_and_browser_values_never_measure(tmp_path):
    send = AsyncMock()
    admission = Admission(str(tmp_path / "actions.db"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: (_ for _ in ()).throw(AssertionError()))) as network:
        for enabled in (False, True):
            app = create_relay(binding=binding(), token=TOKEN, admission=admission, client=network, publish=send, enabled=enabled)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://private.test") as client:
                a = attribution()
                h = headers(a)
                h["authorization"] = "Bearer browser"
                assert (await client.post("/v1/observe", headers=h)).status_code in (403, 404)
                for field in ("tenant", "audience", "source", "value", "duration", "failed"):
                    h = headers(a)
                    h["x-experiment-attribution"] = json.dumps({**json.loads(a.model_dump_json()), field: "browser"})
                    assert (await client.post("/v1/observe", headers=h)).status_code in (400, 404)
                assert (await client.post("/v1/observe", headers=headers(a), content=b"x")).status_code in (400, 404)
    send.assert_not_called()
    admission.close()


def test_replay_budget_and_expiry_survive_reopening(tmp_path):
    path = str(tmp_path / "actions.db")
    a = attribution()
    now = a.issued
    admission = Admission(path)
    assert admission.take(a, now)
    admission.close()
    admission = Admission(path)
    assert not admission.take(a, now)
    assert not admission.take(a, now + 61)
    assert not admission.take(attribution(), now - 1)
    for _ in range(59): assert admission.take(attribution(issued=now), now)
    assert not admission.take(attribution(issued=now), now)
    admission.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 401, 403, 429, 500, 302])
async def test_only_server_failures_count_and_redirects_are_not_followed(tmp_path, status):
    samples, calls = [], []
    async def record(m): samples.append(m)
    def handle(request):
        calls.append(request)
        return httpx.Response(status, headers={"location": "https://foreign.test"})
    admission = Admission(str(tmp_path / "a.db"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as network:
        app = create_relay(binding=binding(), token=TOKEN, admission=admission, client=network, publish=record, enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://relay.test") as client:
            result = await client.post("/v1/observe", headers=headers(attribution()))
    assert result.status_code == (502 if status == 302 else status)
    assert samples[0].failed == (status >= 500 or status == 302)
    assert len(calls) == 1
    admission.close()


@pytest.mark.asyncio
async def test_ingestion_fixed_endpoint_no_redirect_or_scope_body():
    calls = []
    credential = SimpleNamespace(get_token=AsyncMock(return_value=SimpleNamespace(token="platform-only")))
    def upload(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer platform-only"
        assert "Tenant" not in request.content.decode()
        return httpx.Response(302, headers={"location": "https://foreign.test"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(upload)) as client:
        publisher = AzurePublisher("https://test.ingest.monitor.azure.com", "dcr-" + "a" * 32, credential, client)
        assert not await publisher(Measurement(attribution(), int(time.time()), 12.5, False, "session"))
        for endpoint in ("http://test.ingest.monitor.azure.com", "https://foreign.test", "https://test.ingest.monitor.azure.com@foreign.test"):
            with pytest.raises(ValueError): AzurePublisher(endpoint, "dcr-" + "a" * 32, credential, client)
    assert len(calls) == 1
    credential.get_token.assert_awaited_once_with("https://monitor.azure.com/.default")


@pytest.mark.asyncio
async def test_missing_platform_ack_does_not_manufacture_server_sample():
    publish = AsyncMock()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(503))) as client:
        observer = ServerObserver(binding=binding(), endpoint="https://relay.test", token=TOKEN, client=client, publish=publish, enabled=True)
        with pytest.raises(ValueError, match="Unobserved"):
            await observer.request(experiment="signup", variant="join", operation="lookup")
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_default_disabled_observer_and_runner_touch_no_transport(monkeypatch):
    from api.experiment_measurements import run
    monkeypatch.delenv("EXPERIMENT_MEASUREMENTS_ENABLED", raising=False)
    await run()
    publish = AsyncMock()
    observer = ServerObserver(binding=binding(), endpoint="https://relay.test", token=TOKEN, client=None, publish=publish)
    assert await observer.request(experiment="unknown", variant="browser", operation="unknown") is None
    publish.assert_not_called()


@pytest.mark.asyncio
async def test_private_ledger_suffix_etag_limits_and_no_record_data_in_rows(tmp_path):
    samples, calls = [], []
    async def record(m): samples.append(m)
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=b"private ledger", headers={"etag": '"revision-1"'})
    b = binding(operations={"lookup": {"url": "https://owner.test/records/outbox-", "method": "GET",
        "kind": "dependency", "suffix": "sha256", "response_limit": 65536, "timeout_seconds": 3}})
    admission = Admission(str(tmp_path / "a.db"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as network:
        app = create_relay(binding=b, token=TOKEN, admission=admission, client=network, publish=record, enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://relay.test") as client:
            for target in (None, "../admin", "b" * 65, "B" * 64):
                data = json.loads(attribution().model_dump_json()); data["target"] = target
                h = headers(attribution()); h["x-experiment-attribution"] = json.dumps(data)
                assert (await client.post("/v1/observe", headers=h)).status_code == 400
            a = attribution(target="b" * 64)
            result = await client.post("/v1/observe", headers=headers(a))
            assert result.headers["etag"] == '"revision-1"'
            assert result.content == b"private ledger"
            assert (await client.post("/v1/observe", headers=headers(a))).status_code == 409
    assert len(calls) == 1
    assert str(calls[0].url) == "https://owner.test/records/outbox-" + "b" * 64
    assert "bbbb" not in json.dumps(samples[0].rows())
    assert "private ledger" not in json.dumps(samples[0].rows())
    admission.close()


@pytest.mark.asyncio
async def test_outbound_size_bound_and_credential_failure_do_not_change_product_status(tmp_path):
    calls = []
    def upstream(request):
        calls.append(request)
        return httpx.Response(200, content=b"x" * 65537)
    admission = Admission(str(tmp_path / "a.db"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as network:
        app = create_relay(binding=binding(), token=TOKEN, admission=admission, client=network,
                           publish=AsyncMock(side_effect=RuntimeError("private secret")), enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://relay.test") as client:
            result = await client.post("/v1/observe", headers=headers(attribution()))
    assert result.status_code == 502 and result.content == b""
    assert len(calls) == 1
    admission.close()


@pytest.mark.asyncio
async def test_bounded_delivery_drops_overflow_stale_samples_and_stops():
    import asyncio
    from api.domain.experiments.producers import BoundedPublisher
    started, release = asyncio.Event(), asyncio.Event()
    async def sink(sample):
        started.set()
        await release.wait()
    delivery = BoundedPublisher(sink)
    sample = Measurement(attribution(), int(time.time()), 1.0, False, "dependency")
    assert not await delivery(sample)
    async with delivery:
        assert await delivery(sample)
        await started.wait()
        for _ in range(64): assert await delivery(sample)
        assert not await delivery(sample)
    assert not await delivery(sample)


@pytest.mark.asyncio
async def test_session_failures_measured_from_real_http_not_body(tmp_path):
    samples = []
    async def record(m): samples.append(m)
    b = binding(operations={"lookup": {"url": "https://owner.test/session", "method": "POST", "kind": "session"}})
    admission = Admission(str(tmp_path / "a.db"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(401))) as network:
        app = create_relay(binding=b, token=TOKEN, admission=admission, client=network, publish=record, enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://relay.test") as client:
            result = await client.post("/v1/observe", headers=headers(attribution()), content=b'{"failed":true}')
    assert result.status_code == 401
    assert next(row["Value"] for row in samples[0].rows() if row["Metric"] == "session_failure_rate") == 0
    admission.close()


def test_late_attribution_still_occupies_full_admission_rate_window(tmp_path):
    admission = Admission(str(tmp_path / "a.db"))
    for _ in range(60): assert admission.take(attribution(issued=940), 1000)
    assert not admission.take(attribution(issued=1001), 1001)
    assert not admission.take(attribution(issued=1059), 1059)
    assert admission.take(attribution(issued=1060), 1060)
    admission.close()


@pytest.mark.asyncio
async def test_timeout_is_corroborated_within_native_deadline(tmp_path):
    import asyncio
    platform, server = [], []
    async def platform_sink(m): platform.append(m)
    async def server_sink(m): server.append(m)
    async def slow(_):
        await asyncio.sleep(2)
        return httpx.Response(200)
    b = binding(operations={"lookup": {"url": "https://owner.test/lookup", "method": "GET", "kind": "dependency", "timeout_seconds": 1}})
    admission = Admission(str(tmp_path / "a.db"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(slow)) as network:
        app = create_relay(binding=b, token=TOKEN, admission=admission, client=network, publish=platform_sink, enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://relay.test") as client:
            observer = ServerObserver(binding=b, endpoint="https://relay.test", token=TOKEN, client=client, publish=server_sink, enabled=True)
            response = await observer.request(experiment="signup", variant="join", operation="lookup")
    assert response.status_code == 502
    assert len(server) == len(platform) == 1
    assert server[0].failed and platform[0].failed
    assert server[0].duration_ms < 1000
    admission.close()


@pytest.mark.asyncio
async def test_platform_never_replays_cookie_jar_or_decodes_encoded_body(tmp_path):
    calls, samples = [], []
    async def record(m): samples.append(m)
    class Bomb(httpx.AsyncByteStream):
        async def __aiter__(self):
            raise AssertionError("Must refuse encoding before consuming any bytes")
            yield b""
    def upstream(request):
        calls.append(request)
        assert "cookie" not in request.headers
        if len(calls) == 1:
            return httpx.Response(200, headers={"set-cookie": "session=private; Path=/"})
        return httpx.Response(200, headers={"content-encoding": "gzip"}, stream=Bomb())
    admission = Admission(str(tmp_path / "a.db"))
    async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as network:
        app = create_relay(binding=binding(), token=TOKEN, admission=admission, client=network, publish=record, enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://relay.test") as client:
            assert (await client.post("/v1/observe", headers=headers(attribution()))).status_code == 200
            assert network.cookies.get("session") == "private"
            assert (await client.post("/v1/observe", headers=headers(attribution()))).status_code == 502
    assert len(samples) == 2 and samples[1].failed
    admission.close()

@pytest.mark.asyncio
async def test_native_session_exchange_preserves_only_opted_in_bearer_and_fixed_scope(tmp_path):
    captured, platform, server = [], [], []
    b = binding(operations={'session': {'url': 'https://auth.test/auth/scoped-session', 'method': 'POST',
        'kind': 'session', 'forward_session_bearer': True}})
    body = json.dumps({'tenant_id': str(b.tenant), 'organization_id': str(uuid4())}).encode()
    async def upstream(request):
        captured.append(request)
        assert request.headers['authorization'] == 'Bearer native.session.token'
        assert request.content == body
        assert 'cookie' not in request.headers and 'x-experiment-attribution' not in request.headers
        return httpx.Response(403, content=b'current membership refused')
    async def record_platform(m): platform.append(m)
    async def record_server(m): server.append(m)
    admission = Admission(str(tmp_path / 'actions.sqlite'))
    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as network:
            relay = create_relay(binding=b, token=TOKEN, admission=admission, client=network, publish=record_platform, enabled=True)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=relay)) as client:
                observer = ServerObserver(binding=b, endpoint='https://relay.test', token=TOKEN, client=client,
                    publish=record_server, enabled=True)
                result = await observer.request(experiment='signup', variant='join', operation='session',
                    body=body, session_bearer='native.session.token')
                assert result.status_code == 403 and len(captured) == 1
                assert len(platform) == len(server) == 1
                assert all(row['Value'] == 0 for row in server[0].rows() if row['Metric'] != 'latency_ms')
                assert all('token' not in json.dumps(row) and 'tenant_id' not in row for row in platform[0].rows())
                for bad in (None, 'native token', 'a\r\nb', 'x' * 8193):
                    with pytest.raises(ValueError):
                        await observer.request(experiment='signup', variant='join', operation='session', body=body, session_bearer=bad)
                for bad_body in ({'tenant_id': str(uuid4()), 'organization_id': str(uuid4())},
                                 {'tenant_id': str(b.tenant), 'organization_id': '00000000-0000-0000-0000-000000000000'},
                                 {'tenant_id': str(b.tenant), 'organization_id': str(uuid4()), 'source': 'browser'}):
                    a = attribution(operation='session')
                    h = headers(a) | {'x-experiment-session-bearer': 'native.session.token'}
                    response = await client.post('https://relay.test/v1/observe', headers=h, content=json.dumps(bad_body))
                    assert response.status_code == 400
                assert len(captured) == 1
    finally: admission.close()


def test_session_bearer_requires_explicit_fixed_session_post():
    from api.domain.experiments.producers import Operation, session_headers
    for changes in ({'kind': 'dependency'}, {'method': 'GET'}, {'suffix': 'sha256'},
                    {'url': 'https://auth.test/arbitrary'}, {'headers': {'authorization': 'Bearer fixed'}}):
        with pytest.raises(ValueError):
            Operation(**({'url': 'https://auth.test/auth/network-session', 'method': 'POST', 'kind': 'session',
                          'forward_session_bearer': True} | changes))
    b = binding()
    with pytest.raises(ValueError): session_headers(b, b.operations['lookup'], b'', 'native')
