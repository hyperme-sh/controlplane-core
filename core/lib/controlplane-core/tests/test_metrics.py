""" /metrics renders, and RED plus domain series move when the helper is called."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from prometheus_client.parser import text_string_to_metric_families

os.environ["METRICS_OUTBOX_PROBE"] = "0"

from api.infrastructure.metrics import (  # noqa: E402
    finish_host_selection,
    record_host_selection,
    record_lease,
    record_pricing_round,
    register_cache,
    register_heartbeat_view,
    register_outbox_probe,
    render_metrics,
    report_outbox,
    start_metrics_server,
)

REPO = Path(__file__).resolve().parents[4]

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
    register_heartbeat_view(lambda: ({device_id: 12.5, email: 9.0}, 3))
    try:
        assert _sample("hypermesh_hosts_fresh", {}) == 3
        assert _sample(
            "hypermesh_host_heartbeat_age_seconds", {"device_id": device_id}
        ) == 12.5
        text = render_metrics().decode()
    finally:
        register_heartbeat_view(None)
    assert device_id in text
    assert email not in text

    from api.infrastructure.metrics import begin_host_selection, note_host_selection

    grace_before = _sample("hypermesh_host_selection_total", {"outcome": "grace_wait"})
    begin_host_selection()
    note_host_selection("grace_wait")
    finish_host_selection(409)
    assert _sample("hypermesh_host_selection_total", {"outcome": "grace_wait"}) == grace_before + 1


def test_compose_documents_metrics_ports() -> None:
    dev = (REPO / "docker-compose.yaml").read_text()
    vm = (REPO / "docker-compose.vm.yaml").read_text()
    for name, host_port in APP_METRICS_PORTS.items():
        publish = f"127.0.0.1:{host_port}:9100"
        assert publish in dev, name
        assert publish in vm, name
    for text in (dev, vm):
        assert "127.0.0.1:9000:9000" in text
        assert 'KC_METRICS_ENABLED: "true"' in text
        assert "127.0.0.1:9187:9187" in text
        assert "127.0.0.1:9121:9121" in text
        assert "docs/metrics.md" in text
