"""Requires the Panopticon deployment checkout; excluded from package tests."""
import os
from pathlib import Path
REPO = Path(os.environ["PANOPTICON_CHECKOUT"])
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
        assert "docs/metrics.md" in text
        assert text.count("METRICS_OUTBOX_PROBE:") == 1
        assert 'METRICS_OUTBOX_PROBE: "1"' in text
        assert text.count("METRICS_ADDR: compose") == 1
        assert "<service>:9100" in text or "compose DNS" in (REPO / "docs/metrics.md").read_text()
    assert "127.0.0.1:9187:9187" in dev
    assert "127.0.0.1:9121:9121" in dev
    assert "v1.67.0-alpine@sha256:ef9e3c9afa9072b8a4db8bc2b01471cd582a6a1db23760bc5fdb9e3c21842205" in dev
    assert "v0.16.0@sha256:6999a7657e2f2fb0ca6ebf417213eebf6dc7d21b30708c622f6fcb11183a2bb0" in dev
    assert "127.0.0.1:9187:9187" not in vm
    assert "127.0.0.1:9121:9121" not in vm
    assert vm.count('profiles: ["laptop-only"]') == 2
    docs = (REPO / "docs/metrics.md").read_text()
    assert "http://<service>:9100/metrics" in docs
    assert "keycloak:9000" in docs
    assert "debugging" in docs
    assert "#253" in docs and "#251" in docs
