""" /metrics renders, and RED plus domain series move when the helper is called."""

from __future__ import annotations

import logging
import uuid
from pathlib import Path
from socketserver import ThreadingMixIn

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client import exposition as prometheus_exposition
from prometheus_client.parser import text_string_to_metric_families

from api.infrastructure import metrics as metrics_mod
from api.infrastructure.metrics import (
    clear_heartbeat_snapshot,
    compose_network_ipv4,
    export_read_cache_samples,
    finish_host_selection,
    publish_heartbeat_snapshot,
    record_host_selection,
    record_lease,
    record_pricing_round,
    register_cache,
    register_outbox_probe,
    render_metrics,
    report_outbox,
    start_metrics_server,
)



@pytest.fixture(autouse=True)
def _outbox_probe_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the DB probe off for the whole test. Do not set the env at import."""

    monkeypatch.setenv("METRICS_OUTBOX_PROBE", "0")
    register_outbox_probe(None)


APP_METRICS_PORTS = {
    "controlplane-edge": 9100,
    "auth-service": 9101,
    "user-service": 9102,
    "payment-service": 9103,
    "acl-service": 9104,
    "marketplace": 9105,
    "ai-service": 9106,
    "api-keys-service": 9107,
    "template-service": 9108,
    "tennessee-windage-service": 9109,
    "hypermesh-service": 9110,
    "hypermesh-gateway": 9111,
    "turn-service": 9112,
    "oracles-service": 9113,
    "payments-service": 9114,
    "aiuditor-service": 9115,
}


def _sample(name: str, labels: dict[str, str]) -> float:
    text = render_metrics().decode()
    for family in text_string_to_metric_families(text):
        for sample in family.samples:
            if sample.name == name and sample.labels == labels:
                return float(sample.value)
    return 0.0


def test_metrics_endpoint_renders_red_and_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_SHA", "abc1234def")
    monkeypatch.setenv("SERVICE_NAME", "metrics-test")
    secret = "user-secret-id"
    app = FastAPI()

    @app.get("/widgets/{widget_id}")
    def widget(widget_id: str) -> dict[str, str]:
        body = render_metrics().decode()
        assert "http_requests_in_flight " in body or "http_requests_in_flight{" in body
        inflight = _sample("http_requests_in_flight", {})
        assert inflight >= 1
        return {"id": widget_id}

    from api.infrastructure.metrics import install_http_metrics

    install_http_metrics(app)
    before = _sample(
        "http_requests_total",
        {"method": "GET", "route": "/widgets/{widget_id}", "status": "200"},
    )
    client = TestClient(app)
    response = client.get(f"/widgets/{secret}")
    assert response.status_code == 200
    after = _sample(
        "http_requests_total",
        {"method": "GET", "route": "/widgets/{widget_id}", "status": "200"},
    )
    assert after == before + 1
    duration = _sample("http_request_duration_seconds_count", {"method": "GET", "route": "/widgets/{widget_id}"})
    assert duration >= 1

    missed = client.get(f"/no-such/{secret}")
    assert missed.status_code == 404
    unmatched = _sample(
        "http_requests_total",
        {"method": "GET", "route": "unmatched", "status": "404"},
    )
    assert unmatched >= 1

    port = start_metrics_server(port=0, addr="127.0.0.1")
    import urllib.request

    rendered = ""
    last_error: Exception | None = None
    for _ in range(20):
        try:
            rendered = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/metrics", timeout=2
            ).read().decode()
            break
        except Exception as exc:
            last_error = exc
    else:
        raise AssertionError(f"/metrics did not answer on {port}: {last_error}")
    assert "http_requests_total" in rendered
    assert "/widgets/{widget_id}" in rendered
    assert secret not in rendered
    assert 'build_info{git_sha="abc1234def",service="metrics-test"}' in rendered or (
        'git_sha="abc1234def"' in rendered and 'service="metrics-test"' in rendered
    )
    assert "process_cpu_seconds_total" in rendered or "process_resident_memory_bytes" in rendered
    assert isinstance(metrics_mod._server, ThreadingMixIn)
    handler = getattr(prometheus_exposition, "_SilentHandler", None)
    assert handler is not None, "prometheus_client._SilentHandler missing; socket timeout cannot be set"
    assert handler.timeout == 10.0


def test_domain_metrics_increment_without_user_labels() -> None:
    register_outbox_probe(None)
    email = "ada@example.com"
    failed_before = _sample(
        "hypermesh_leases_total",
        {"event": "failed", "reason": "recorded_host_failure"},
    )
    other_before = _sample("hypermesh_leases_total", {"event": "failed", "reason": "other"})
    record_lease("failed", "recorded host failure")
    record_lease("failed", email)
    record_lease("not-an-event", "none")
    assert _sample(
        "hypermesh_leases_total",
        {"event": "failed", "reason": "recorded_host_failure"},
    ) == failed_before + 1
    assert _sample("hypermesh_leases_total", {"event": "failed", "reason": "other"}) == other_before + 1
    assert email not in render_metrics().decode()

    created_before = _sample("hypermesh_leases_total", {"event": "created", "reason": "none"})
    record_lease("created")
    assert _sample("hypermesh_leases_total", {"event": "created", "reason": "none"}) == created_before + 1

    success_before = _sample("hypermesh_host_selection_total", {"outcome": "success"})
    record_host_selection("success")
    record_host_selection("raw-path")
    assert _sample("hypermesh_host_selection_total", {"outcome": "success"}) == success_before + 1

    runs_before = _sample("hypermesh_pricing_rounds_total", {"result": "run"})
    failures_before = _sample("hypermesh_pricing_rounds_total", {"result": "failure"})
    record_pricing_round(failed=False)
    record_pricing_round(failed=True)
    assert _sample("hypermesh_pricing_rounds_total", {"result": "run"}) == runs_before + 1
    assert _sample("hypermesh_pricing_rounds_total", {"result": "failure"}) == failures_before + 1
    assert _sample("hypermesh_pricing_round_last_success_timestamp_seconds", {}) > 0

    report_outbox(4, 2)
    assert _sample("controlplane_events_outbox_depth", {}) == 4
    assert _sample("controlplane_events_outbox_dead", {}) == 2

    cache = register_cache("unit_cache")
    hits_before = _sample("cache_lookups_total", {"cache": "unit_cache", "result": "hit"})
    misses_before = _sample("cache_lookups_total", {"cache": "unit_cache", "result": "miss"})
    cache.hit()
    cache.miss()
    assert _sample("cache_lookups_total", {"cache": "unit_cache", "result": "hit"}) == hits_before + 1
    assert _sample("cache_lookups_total", {"cache": "unit_cache", "result": "miss"}) == misses_before + 1
    with pytest.raises(ValueError):
        register_cache(email)

    device_id = str(uuid.uuid4())
    clear_heartbeat_snapshot()
    assert "hypermesh_hosts_fresh" not in render_metrics().decode()
    seen = metrics_mod.time.time() - 12.5
    publish_heartbeat_snapshot({device_id: (seen, 180.0), email: (seen, 180.0)})
    try:
        assert _sample("hypermesh_hosts_fresh", {}) == 1
        age = _sample(
            "hypermesh_host_heartbeat_age_seconds", {"device_id": device_id}
        )
        assert 12.5 <= age < 20
        text = render_metrics().decode()
    finally:
        clear_heartbeat_snapshot()
    assert device_id in text
    assert email not in text

    from api.infrastructure.metrics import begin_host_selection, note_host_selection

    grace_before = _sample("hypermesh_host_selection_total", {"outcome": "grace_wait"})
    begin_host_selection()
    note_host_selection("grace_wait")
    finish_host_selection(409)
    assert _sample("hypermesh_host_selection_total", {"outcome": "grace_wait"}) == grace_before + 1


def test_heartbeat_age_grows_at_scrape_without_a_new_publish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stalled fleet-health loop still moves the age gauge past 180s."""

    device_id = str(uuid.uuid4())
    clear_heartbeat_snapshot()
    seen = 1_700_000_000.0
    clock = {"now": seen + 100.0}
    monkeypatch.setattr(metrics_mod.time, "time", lambda: clock["now"])
    publish_heartbeat_snapshot({device_id: (seen, 180.0)})
    try:
        assert _sample(
            "hypermesh_host_heartbeat_age_seconds", {"device_id": device_id}
        ) == 100.0
        assert _sample("hypermesh_hosts_fresh", {}) == 1
        clock["now"] = seen + 181.0
        assert _sample(
            "hypermesh_host_heartbeat_age_seconds", {"device_id": device_id}
        ) == 181.0
        assert _sample("hypermesh_hosts_fresh", {}) == 0
    finally:
        clear_heartbeat_snapshot()


def test_missing_silent_handler_skips_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A prometheus_client release that drops the private handler must not raise."""

    monkeypatch.delattr(prometheus_exposition, "_SilentHandler", raising=False)
    with caplog.at_level(logging.WARNING, logger="api.infrastructure.metrics"):
        metrics_mod._apply_handler_timeout()
    assert "metrics socket timeout not set" in caplog.text


def test_bind_failure_logs_and_returns(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(metrics_mod, "_server", None)

    def boom(*_args: object, **_kwargs: object) -> None:
        raise OSError(98, "address already in use")

    monkeypatch.setattr(metrics_mod, "start_http_server", boom)
    with caplog.at_level(logging.ERROR, logger="api.infrastructure.metrics"):
        assert start_metrics_server(port=9100, addr="127.0.0.1") == -1
    assert "continuing without /metrics" in caplog.text


def test_compose_addr_skips_wireguard() -> None:
    addr = compose_network_ipv4(
        {"lo": "127.0.0.1", "wg-hm": "10.77.0.1", "eth0": "172.18.0.8"}
    )
    assert addr == "172.18.0.8"
    with pytest.raises(OSError):
        compose_network_ipv4({"lo": "127.0.0.1", "wg-hm": "10.77.0.1"})


def test_outbox_probe_is_opt_in_with_statement_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://metrics:secret@postgres/controlplane")
    metrics_mod._maybe_register_outbox_probe()
    assert metrics_mod._outbox_probe is None

    captured: dict[str, object] = {}

    def fake_engine(url: str, **kwargs: object) -> object:
        captured["url"] = url
        captured["kwargs"] = kwargs
        raise RuntimeError("stop before connect")

    monkeypatch.setenv("METRICS_OUTBOX_PROBE", "1")
    monkeypatch.setattr(metrics_mod, "_outbox_probe", None)
    monkeypatch.setattr("sqlalchemy.create_engine", fake_engine)
    metrics_mod._maybe_register_outbox_probe()
    probe = metrics_mod._outbox_probe
    assert probe is not None
    with pytest.raises(RuntimeError, match="stop before connect"):
        probe()
    assert captured["url"] == "postgresql+psycopg://metrics:secret@postgres/controlplane"
    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["connect_args"]["connect_timeout"] == 2
    assert kwargs["connect_args"]["options"] == "-c statement_timeout=2000"
    register_outbox_probe(None)




def test_export_read_cache_samples_renders_absolute_counters() -> None:
    class _Sample:
        def __init__(
            self,
            name: str,
            labels: tuple[tuple[str, str], ...],
            value: int,
        ) -> None:
            self.name = name
            self.labels = labels
            self.value = value

    class _Cache:
        def samples(self) -> tuple[_Sample, ...]:
            return (
                _Sample("hypermesh_read_cache_hits_total", (("scope", "nodes"),), 3),
                _Sample("hypermesh_read_cache_misses_total", (("scope", "catalog"),), 1),
                _Sample("hypermesh_read_cache_invalidations_total", (), 2),
                _Sample("hypermesh_read_cache_hits_total", (("scope", "tenant-flood"),), 9),
                _Sample("other_total", (), 4),
            )

    export_read_cache_samples(_Cache())
    try:
        assert _sample("hypermesh_read_cache_hits_total", {"scope": "nodes"}) == 3
        assert _sample("hypermesh_read_cache_misses_total", {"scope": "catalog"}) == 1
        assert _sample("hypermesh_read_cache_invalidations_total", {}) == 2
        assert _sample("hypermesh_read_cache_hits_total", {"scope": "tenant-flood"}) == 0
        assert "other_total" not in render_metrics().decode()
    finally:
        export_read_cache_samples(None)
    text = render_metrics().decode()
    assert "hypermesh_read_cache_hits_total" not in text
    assert "hypermesh_read_cache_invalidations_total" not in text


def test_outbox_probe_delegates_to_event_owner_and_reuses_engine(monkeypatch):
    from contextlib import contextmanager

    calls = []
    class Connection:
        def execute(self, sql):
            calls.append(str(sql))

    connection = Connection()

    class Engine:
        @contextmanager
        def connect(self):
            yield connection

    def create_engine(url, **kwargs):
        calls.append("engine")
        return Engine()

    def counts(conn):
        assert conn is connection
        calls.append("owner")
        return (3, 1)

    monkeypatch.setattr("sqlalchemy.create_engine", create_engine)
    monkeypatch.setattr("controlplane_events.store.delivery_counts_sync", counts)
    probe = metrics_mod._database_outbox_probe("postgresql://postgres/controlplane")
    assert calls == []  # Lazy: installing metrics opens no database connection.
    assert probe() == (3, 1)
    assert probe() == (3, 1)
    assert calls == ["engine", "SET LOCAL statement_timeout = 2000", "owner",
                     "SET LOCAL statement_timeout = 2000", "owner"]
