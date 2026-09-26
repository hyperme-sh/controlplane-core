"""Prometheus metrics for every Python service.

RED series, process collectors, and ``build_info`` are process-wide. Domain
counters are recorded through the functions in this module so product code
and Stripe stay behind their own clients. The scrape endpoint is a separate
HTTP server (``METRICS_PORT``, default 9100), not a route on the public app.

Label values are bounded: route templates, a fixed reason-code set, and a
UUID device id only on the fleet heartbeat gauge. Secrets and user
identifiers are not label values.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from typing import Protocol

from prometheus_client import (
    REGISTRY,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)
from prometheus_client.core import GaugeMetricFamily
from prometheus_client.exposition import _SilentHandler
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Idle scrape clients must not hold a worker thread. prometheus_client's
# handler applies this to the accepted socket.
_METRICS_REQUEST_TIMEOUT_S = 10.0
# METRICS_ADDR=compose binds the compose NIC, not 0.0.0.0 and not WireGuard.
_COMPOSE_ADDR = "compose"
_SKIP_IFACE_PREFIXES = (
    "lo",
    "wg",
    "tun",
    "tap",
    "tailscale",
    "docker",
    "br-",
    "veth",
    "virbr",
    "sit",
)
_PREFERRED_IFACE_PREFIXES = ("eth", "ens", "enp", "eno")

# Default histogram buckets for request latency, in seconds.
_DURATION_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10)

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})
_SHA = re.compile(r"^[0-9a-fA-F]{7,40}$")
_TOKEN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_CACHE_NAME = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_DEVICE_ID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

LEASE_EVENTS = frozenset({"created", "started", "ended", "failed"})
LEASE_REASONS = frozenset(
    {
        "none",
        "schedule_hold",
        "not_green",
        "recorded_host_failure",
        "cert_failed",
        "lease_stop_failed",
        "path_b_failed",
        "compact_cert_failed",
        "instance_check_failed",
        "deenroll",
        "violation",
        "other",
    }
)
_LEASE_REASON_ALIASES = {
    "": "none",
    "none": "none",
    "schedule_hold": "schedule_hold",
    "not_green": "not_green",
    "recorded host failure": "recorded_host_failure",
    "recorded_host_failure": "recorded_host_failure",
    "cert failed before window end": "cert_failed",
    "cert_failed": "cert_failed",
    "lease_stop_failed": "lease_stop_failed",
    "path_b_failed": "path_b_failed",
    "compact_cert_failed": "compact_cert_failed",
    "instance_check_failed": "instance_check_failed",
    "device_deenrolled": "deenroll",
    "deenroll": "deenroll",
    "violation": "violation",
    "other": "other",
}

HOST_SELECTION_OUTCOMES = frozenset(
    {"success", "grace_wait", "transient_503", "conflict_409"}
)

HTTP_REQUESTS = Counter(
    "http_requests_total",
    "HTTP requests by method, route template, and status.",
    ("method", "route", "status"),
)
HTTP_DURATION = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds by method and route template.",
    ("method", "route"),
    buckets=_DURATION_BUCKETS,
)
HTTP_IN_FLIGHT = Gauge(
    "http_requests_in_flight",
    "HTTP requests currently being served.",
)
BUILD_INFO = Gauge(
    "build_info",
    "Build metadata for this process. Value is always 1.",
    ("git_sha", "service"),
)
LEASES = Counter(
    "hypermesh_leases_total",
    "Lease lifecycle events by outcome and bounded reason code.",
    ("event", "reason"),
)
HOST_SELECTION = Counter(
    "hypermesh_host_selection_total",
    "Host selection outcomes.",
    ("outcome",),
)
PRICING_ROUNDS = Counter(
    "hypermesh_pricing_rounds_total",
    "Pricing rounds that called the engine.",
    ("result",),
)
PRICING_LAST_SUCCESS = Gauge(
    "hypermesh_pricing_round_last_success_timestamp_seconds",
    "Unix time of the last pricing round that persisted.",
)
CACHE_LOOKUPS = Counter(
    "cache_lookups_total",
    "Cache lookups by cache name and result.",
    ("cache", "result"),
)

_heartbeat_lock = threading.Lock()
# None until the fleet-health loop publishes. Absence is not a fresh count of 0.
_heartbeat_snapshot: tuple[tuple[tuple[str, float], ...], int] | None = None
_outbox_lock = threading.Lock()
_outbox_probe: Callable[[], tuple[int, int]] | None = None
_outbox_depth = 0
_outbox_dead = 0
_outbox_ready = False
_collectors_ready = False
_server_lock = threading.Lock()
_server = None
_host_selection: ContextVar[str | None] = ContextVar(
    "panopticon_host_selection", default=None
)


class CacheObserver(Protocol):
    """Hit/miss recorder. ``register_cache`` returns one of these.

    A later cache implementation calls ``hit`` and ``miss``. The name is a
    static token, not a key, tenant, or user id.
    """

    def hit(self) -> None: ...

    def miss(self) -> None: ...


class _CacheRecorder:
    def __init__(self, name: str) -> None:
        self._name = name

    def hit(self) -> None:
        CACHE_LOOKUPS.labels(cache=self._name, result="hit").inc()

    def miss(self) -> None:
        CACHE_LOOKUPS.labels(cache=self._name, result="miss").inc()


class _HeartbeatCollector:
    """Read the snapshot the fleet-health loop published. Do not touch the store."""

    def collect(self):
        with _heartbeat_lock:
            snap = _heartbeat_snapshot
        if snap is None:
            return
        ages, fresh = snap
        fresh_family = GaugeMetricFamily(
            "hypermesh_hosts_fresh",
            "Hosts whose last heartbeat is inside the stale window.",
        )
        fresh_family.add_metric([], float(fresh))
        yield fresh_family
        age_family = GaugeMetricFamily(
            "hypermesh_host_heartbeat_age_seconds",
            "Seconds since the host's last heartbeat.",
            labels=("device_id",),
        )
        for device_id, seconds in ages:
            age_family.add_metric([device_id], seconds)
        yield age_family


class _OutboxCollector:
    def collect(self):
        probe = _outbox_probe
        if probe is not None:
            try:
                depth, dead = probe()
            except Exception:
                logger.exception("event outbox metrics probe failed")
            else:
                _remember_outbox(depth, dead)
        with _outbox_lock:
            if not _outbox_ready:
                return
            depth = _outbox_depth
            dead = _outbox_dead
        depth_family = GaugeMetricFamily(
            "controlplane_events_outbox_depth",
            "Event deliveries still pending or claimed.",
        )
        depth_family.add_metric([], float(depth))
        yield depth_family
        dead_family = GaugeMetricFamily(
            "controlplane_events_outbox_dead",
            "Event deliveries that exhausted attempts.",
        )
        dead_family.add_metric([], float(dead))
        yield dead_family


def _ensure_collectors() -> None:
    global _collectors_ready
    if _collectors_ready:
        return
    REGISTRY.register(_HeartbeatCollector())
    REGISTRY.register(_OutboxCollector())
    _collectors_ready = True


_ensure_collectors()


def _method_label(raw: str) -> str:
    method = raw.upper()
    if method in _METHODS:
        return method
    return "OTHER"


def _status_label(code: int) -> str:
    if 100 <= code <= 599:
        return str(code)
    return "500"


def _route_template(scope: Scope) -> str:
    route = scope.get("route")
    path = getattr(route, "path", None)
    if isinstance(path, str) and path and len(path) <= 160 and " " not in path:
        return path
    return "unmatched"


def _git_sha() -> str:
    for name in ("GIT_SHA", "SOURCE_SHA", "HYPERMESH_SOURCE_SHA"):
        raw = os.environ.get(name, "").strip()
        if _SHA.fullmatch(raw):
            return raw.lower()
    tag = os.environ.get("PANOPTICON_IMAGE_TAG", "").strip()
    if _SHA.fullmatch(tag):
        return tag.lower()
    return "unknown"


def _service_label(explicit: str | None) -> str:
    raw = (explicit or os.environ.get("SERVICE_NAME") or "unknown").strip()
    if _TOKEN.fullmatch(raw):
        return raw
    return "unknown"


def configure_build_info(service: str | None = None) -> None:
    """Set ``build_info{git_sha,service}`` to 1 for this process."""

    BUILD_INFO.labels(git_sha=_git_sha(), service=_service_label(service)).set(1)


class RedMetricsMiddleware:
    """Count RED metrics using the matched route template, not the raw path."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method = _method_label(str(scope.get("method") or ""))
        status_code = 500
        started = time.perf_counter()
        HTTP_IN_FLIGHT.inc()

        async def send_status(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = int(message["status"])
            await send(message)

        try:
            await self.app(scope, receive, send_status)
        finally:
            HTTP_IN_FLIGHT.dec()
            route = _route_template(scope)
            HTTP_REQUESTS.labels(
                method=method, route=route, status=_status_label(status_code)
            ).inc()
            HTTP_DURATION.labels(method=method, route=route).observe(
                time.perf_counter() - started
            )


def install_http_metrics(app: object, *, service: str | None = None) -> None:
    """Attach RED middleware and publish build_info. Does not bind a port."""

    configure_build_info(service)
    add = getattr(app, "add_middleware", None)
    if add is None:
        raise TypeError("app does not accept middleware")
    add(RedMetricsMiddleware)
    _maybe_register_outbox_probe()


def record_lease(event: str, reason: str | None = None) -> None:
    """Count one lease transition. Unknown reasons collapse to ``other``."""

    if event not in LEASE_EVENTS:
        return
    code = _LEASE_REASON_ALIASES.get((reason or "").strip().lower(), "other")
    if code not in LEASE_REASONS:
        code = "other"
    LEASES.labels(event=event, reason=code).inc()


def record_host_selection(outcome: str) -> None:
    if outcome not in HOST_SELECTION_OUTCOMES:
        return
    HOST_SELECTION.labels(outcome=outcome).inc()


def clear_host_selection() -> None:
    """Drop a selection note left on this task."""

    _host_selection.set(None)


def begin_host_selection() -> None:
    """Mark that this task entered host selection. No outcome yet."""

    if _host_selection.get() is None:
        _host_selection.set("")


def note_host_selection(outcome: str) -> None:
    """Override the selection outcome. Ignored unless ``outcome`` is known."""

    if outcome in HOST_SELECTION_OUTCOMES:
        _host_selection.set(outcome)


def finish_host_selection(status_code: int) -> None:
    """Record the selection outcome once, if selection began on this task."""

    noted = _host_selection.get()
    _host_selection.set(None)
    if noted is None:
        return
    if noted in HOST_SELECTION_OUTCOMES:
        record_host_selection(noted)
        return
    if status_code < 300:
        record_host_selection("success")
    elif status_code == 503:
        record_host_selection("transient_503")
    elif status_code == 409:
        record_host_selection("conflict_409")


def record_pricing_round(*, failed: bool) -> None:
    if failed:
        PRICING_ROUNDS.labels(result="failure").inc()
        return
    PRICING_ROUNDS.labels(result="run").inc()
    PRICING_LAST_SUCCESS.set(time.time())


def publish_heartbeat_snapshot(ages: Mapping[str, float], fresh: int) -> None:
    """Publish an immutable heartbeat snapshot for the scrape thread.

    Call this from the fleet-health loop, on the thread that mutates the
    store. The collector only reads the copy under ``_heartbeat_lock``.
    Non-UUID device ids are dropped here. A later failed snapshot must not
    call this with zeros; leave the previous copy in place.
    """

    frozen: list[tuple[str, float]] = []
    for device_id, age in ages.items():
        if not isinstance(device_id, str) or _DEVICE_ID.fullmatch(device_id) is None:
            continue
        try:
            seconds = float(age)
        except (TypeError, ValueError):
            continue
        if seconds < 0:
            seconds = 0.0
        frozen.append((device_id, seconds))
    frozen.sort()
    with _heartbeat_lock:
        global _heartbeat_snapshot
        _heartbeat_snapshot = (tuple(frozen), int(fresh))


def clear_heartbeat_snapshot() -> None:
    """Drop the published snapshot. The next scrape omits the heartbeat gauges."""

    global _heartbeat_snapshot
    with _heartbeat_lock:
        _heartbeat_snapshot = None


def register_cache(name: str) -> CacheObserver:
    """Return a hit/miss recorder. ``name`` is a static token, not a key."""

    if _CACHE_NAME.fullmatch(name) is None:
        raise ValueError("cache name must be a short static token")
    return _CacheRecorder(name)


def report_outbox(depth: int, dead: int) -> None:
    """Set outbox gauges. A registered probe overwrites these on scrape."""

    _remember_outbox(depth, dead)


def register_outbox_probe(probe: Callable[[], tuple[int, int]] | None) -> None:
    """Scrape calls ``probe`` for ``(depth, dead)``. ``None`` disables it."""

    global _outbox_probe
    _outbox_probe = probe


def _remember_outbox(depth: int, dead: int) -> None:
    global _outbox_depth, _outbox_dead, _outbox_ready
    with _outbox_lock:
        _outbox_depth = max(0, int(depth))
        _outbox_dead = max(0, int(dead))
        _outbox_ready = True


def _sync_postgres_url(url: str) -> str:
    if "+asyncpg" in url:
        return url.replace("+asyncpg", "+psycopg", 1)
    if url.startswith("postgresql+psycopg2://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql+psycopg2://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    return url


_OUTBOX_SQL = """
SELECT
  COUNT(*) FILTER (WHERE status IN ('pending', 'claimed')) AS depth,
  COUNT(*) FILTER (WHERE status = 'dead') AS dead
FROM controlplane_event_deliveries
"""


def _maybe_register_outbox_probe() -> None:
    """Opt in with ``METRICS_OUTBOX_PROBE=1`` on one service. Default is off."""

    if os.environ.get("METRICS_OUTBOX_PROBE", "0") != "1":
        return
    if _outbox_probe is not None:
        return
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        return
    register_outbox_probe(_database_outbox_probe(url))


def _database_outbox_probe(url: str) -> Callable[[], tuple[int, int]]:
    holder: dict[str, object] = {}

    def probe() -> tuple[int, int]:
        engine = holder.get("engine")
        if engine is None:
            from sqlalchemy import create_engine, text

            engine = create_engine(
                _sync_postgres_url(url),
                pool_pre_ping=True,
                pool_size=1,
                max_overflow=0,
                connect_args={
                    "connect_timeout": 2,
                    "options": "-c statement_timeout=2000",
                },
            )
            holder["engine"] = engine
            holder["sql"] = text(_OUTBOX_SQL)
            holder["timeout"] = text("SET LOCAL statement_timeout = 2000")
        sql = holder["sql"]
        with engine.connect() as conn:  # type: ignore[union-attr]
            conn.execute(holder["timeout"])  # type: ignore[arg-type]
            row = conn.execute(sql).one()
        return int(row.depth), int(row.dead)

    return probe


def _wg_gateway_ip() -> str | None:
    raw = os.environ.get("HYPERMESH_WG_GATEWAY_ADDR", "10.77.0.1/24").strip()
    ip = raw.split("/", 1)[0].strip()
    return ip or None


def _skipped_iface(name: str, addr: str, wg_ip: str | None) -> bool:
    if any(name == prefix or name.startswith(prefix) for prefix in _SKIP_IFACE_PREFIXES):
        return True
    if addr.startswith("127."):
        return True
    return bool(wg_ip and addr == wg_ip)


def _local_ipv4_by_iface() -> dict[str, str]:
    import fcntl
    import socket
    import struct

    try:
        with open("/proc/net/dev", encoding="ascii") as handle:
            names = [line.split(":", 1)[0].strip() for line in handle.readlines()[2:]]
    except OSError as exc:
        raise OSError("cannot list network interfaces") from exc
    found: dict[str, str] = {}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        for name in names:
            try:
                packed = fcntl.ioctl(
                    sock.fileno(),
                    0x8915,  # SIOCGIFADDR
                    struct.pack("256s", name.encode("ascii")[:15]),
                )
            except OSError:
                continue
            found[name] = socket.inet_ntoa(packed[20:24])
    finally:
        sock.close()
    return found


def compose_network_ipv4(ifaces: Mapping[str, str] | None = None) -> str:
    """IPv4 on the compose NIC. Skips loopback, WireGuard, and tunnel ifaces."""

    found = dict(ifaces) if ifaces is not None else _local_ipv4_by_iface()
    wg_ip = _wg_gateway_ip()
    preferred: list[tuple[str, str]] = []
    rest: list[tuple[str, str]] = []
    for name, addr in found.items():
        if _skipped_iface(name, addr, wg_ip):
            continue
        if name.startswith(_PREFERRED_IFACE_PREFIXES):
            preferred.append((name, addr))
        else:
            rest.append((name, addr))
    chosen = sorted(preferred) or sorted(rest)
    if not chosen:
        raise OSError("no compose-network IPv4 for the metrics bind")
    return chosen[0][1]


def start_metrics_server(port: int | None = None, addr: str | None = None) -> int:
    """Serve ``GET /metrics`` on a threaded daemon server. Returns the bound port.

    ``METRICS_ENABLED=0`` does not bind. A second call returns the port
    already bound in this process. A bind failure is logged and returns
    ``-1``; it does not propagate to ``main()``. ``METRICS_ADDR=compose``
    binds the compose-network address so a WireGuard address on the same
    netns is not a listener.
    """

    global _server
    if os.environ.get("METRICS_ENABLED", "1") == "0":
        return -1
    with _server_lock:
        if _server is not None:
            return int(_server.server_address[1])
        bind_port = int(
            port if port is not None else os.environ.get("METRICS_PORT", "9100")
        )
        bind_addr = addr if addr is not None else os.environ.get("METRICS_ADDR", "0.0.0.0")
        try:
            if bind_addr == _COMPOSE_ADDR:
                bind_addr = compose_network_ipv4()
            _SilentHandler.timeout = _METRICS_REQUEST_TIMEOUT_S
            started = start_http_server(bind_port, addr=bind_addr, registry=REGISTRY)
            httpd = started[0] if isinstance(started, tuple) else started
            if httpd is None:
                raise OSError("metrics server did not return a socket")
            _server = httpd
            return int(httpd.server_address[1])
        except Exception:
            logger.exception(
                "metrics server failed to bind %s:%s; continuing without /metrics",
                bind_addr,
                bind_port,
            )
            return -1


def render_metrics(registry: CollectorRegistry = REGISTRY) -> bytes:
    """Render the exposition text the ``/metrics`` handler returns."""

    return generate_latest(registry)
