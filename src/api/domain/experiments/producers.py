"""Private measurement transport. Never install on the public Core application."""
import asyncio
import hmac
import json
import re
import sqlite3
import time
from dataclasses import dataclass
from contextlib import asynccontextmanager
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import FastAPI, Request
from pydantic import Field, model_validator
from starlette.responses import Response

from api.domain.experiments.control import ClosedModel, Label, Second

MAX_BODY = 65536
MAX_RESPONSE = 2 * 1024 * 1024


class Attribution(ClosedModel):
    action: UUID
    experiment: Label
    variant: Label
    operation: Label
    issued: Second
    target: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$", max_length=64)

    @model_validator(mode="after")
    def generated_id(self):
        if self.action.version != 4:
            raise ValueError("Generated action required")
        return self


class Operation(ClosedModel):
    url: str = Field(max_length=2048)
    method: Literal["GET", "POST"]
    kind: Literal["dependency", "session"]
    suffix: Literal["none", "sha256"] = "none"
    timeout_seconds: int = Field(default=3, ge=1, le=10)
    response_limit: int = Field(default=65536, ge=1, le=MAX_RESPONSE)
    forward_session_bearer: bool = False
    headers: dict[str, str] = Field(default_factory=dict, max_length=8)

    @model_validator(mode="after")
    def fixed_destination(self):
        url = urlsplit(self.url)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.fragment or url.query:
            raise ValueError("Fixed HTTPS destination required")
        if any(not re.fullmatch(r"[a-z0-9-]{1,64}", k) or k in ("host", "content-length", "transfer-encoding", "connection", "accept-encoding")
               or not 1 <= len(v) <= 8192 or any(ord(c) < 32 or ord(c) > 126 for c in v)
               for k, v in self.headers.items()):
            raise ValueError("Invalid fixed operation headers")
        if self.forward_session_bearer and (self.kind != "session" or self.method != "POST"
                or self.suffix != "none" or url.path not in ("/auth/scoped-session", "/auth/network-session")
                or "authorization" in self.headers):
            raise ValueError("Explicit session exchange binding required")
        return self


class Binding(ClosedModel):
    tenant: UUID
    audience: Literal["s3r.ch", "hyperme.sh", "portal"]
    experiments: dict[Label, tuple[Label, ...]] = Field(min_length=1, max_length=8)
    operations: dict[Label, Operation] = Field(min_length=1, max_length=16)
    # At most 60 operations/minute per process; 1/N selection uses generated IDs.
    sample_every: int = Field(ge=1, le=10000, default=100)

    @model_validator(mode="after")
    def bounded(self):
        if not self.tenant.int or any(not 2 <= len(v) <= 8 or len(set(v)) != len(v) for v in self.experiments.values()):
            raise ValueError("Invalid producer binding")
        return self

    def permits(self, attribution):
        return (attribution.variant in self.experiments.get(attribution.experiment, ())
                and attribution.operation in self.operations
                and (attribution.target is not None) == (self.operations[attribution.operation].suffix == "sha256"))


def session_headers(binding, operation, body, bearer):
    """One opt-in native bearer, never arbitrary browser headers or cookies."""
    if not operation.forward_session_bearer:
        if bearer is not None:
            raise ValueError("Session bearer not configured")
        return {}
    if not isinstance(bearer, str) or not re.fullmatch(r"[A-Za-z0-9._~+/=-]{1,8192}", bearer):
        raise ValueError("Native session bearer required")
    if len(body) > 4096:
        raise ValueError("Session body exceeds bound")
    try:
        payload = json.loads(body)
        scoped = urlsplit(operation.url).path == "/auth/scoped-session"
        fields = {"tenant_id", "organization_id"} if scoped else {"tenant_id"}
        if (not isinstance(payload, dict) or set(payload) != fields
                or payload["tenant_id"] != str(binding.tenant)
                or scoped and (str(UUID(payload["organization_id"])) != payload["organization_id"]
                               or not UUID(payload["organization_id"]).int)):
            raise ValueError("Session body scope mismatch")
    except (ValueError, TypeError, AttributeError) as error:
        raise ValueError("Session body scope mismatch") from error
    return {"authorization": "Bearer " + bearer}


@dataclass(frozen=True)
class Measurement:
    attribution: Attribution
    observed: int
    duration_ms: float
    failed: bool
    kind: str
    session_failed: bool | None = None

    def rows(self):
        a = self.attribution
        values = {"latency_ms": self.duration_ms, "error_rate": float(self.failed),
                  self.kind + "_failure_rate": float(self.session_failed if self.kind == "session" and self.session_failed is not None else self.failed)}
        return [{"ActionId": str(a.action), "Experiment": a.experiment, "Variant": a.variant,
                 "Metric": metric, "ObservedAt": self.observed, "Value": value} for metric, value in values.items()]


class AzurePublisher:
    """One deployment DCR/principal per source. Scope is absent from upload rows."""
    def __init__(self, endpoint, rule, credential, client):
        url = urlsplit(endpoint)
        if (url.scheme != "https" or not url.hostname or not url.hostname.endswith(".ingest.monitor.azure.com")
                or url.username or url.password or url.port or url.path not in ("", "/") or url.query or url.fragment
                or not re.fullmatch(r"dcr-[a-f0-9]{32}", rule)):
            raise ValueError("Explicit Azure ingestion binding required")
        self.url = endpoint.rstrip("/") + f"/dataCollectionRules/{rule}/streams/Custom-ExperimentGuardrail?api-version=2023-01-01"
        self.credential, self.client = credential, client

    async def __call__(self, measurement):
        # No background retry, unbounded queue or availability change on failure.
        try:
            if not int(time.time()) - 60 <= measurement.observed <= int(time.time()):
                return False
            async with asyncio.timeout(3):
                token = await self.credential.get_token("https://monitor.azure.com/.default")
                if not isinstance(token.token, str) or not 1 <= len(token.token) <= 16384:
                    return False
                async with self.client.stream("POST", self.url, json=measurement.rows(),
                        headers={"Authorization": "Bearer " + token.token}, timeout=2, follow_redirects=False) as reply:
                    return reply.status_code == 204
        except Exception:
            return False


class Admission:
    """Durable bounded replay and rate budget. File must be on a private volume."""
    def __init__(self, path):
        self.db = sqlite3.connect(path)
        self.db.execute("CREATE TABLE IF NOT EXISTS actions (id TEXT PRIMARY KEY, expires INTEGER NOT NULL, admitted INTEGER NOT NULL)")

    def take(self, attribution, now):
        if not now - 60 <= attribution.issued <= now:
            return False
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            self.db.execute("DELETE FROM actions WHERE expires < ? AND admitted <= ?", (now, now - 60))
            if self.db.execute("SELECT COUNT(*) FROM actions WHERE admitted > ?", (now - 60,)).fetchone()[0] >= 60:
                return False
            try:
                self.db.execute("INSERT INTO actions VALUES (?, ?, ?)",
                                (str(attribution.action), attribution.issued + 60, now))
            except sqlite3.IntegrityError:
                return False
        return True

    def close(self):
        self.db.close()


async def _read(stream, limit):
    data = bytearray()
    async for chunk in stream:
        data.extend(chunk)
        if len(data) > limit:
            raise ValueError("Operation exceeds bound")
    return bytes(data)


@asynccontextmanager
async def _forward(client, method, url, body, headers):
    # Construct directly: AsyncClient.build_request/stream merge its cookie jar.
    # No stored response cookie or client default header becomes authority.
    request = httpx.Request(method, url, content=body, headers=headers)
    response = await client.send(request, stream=True, follow_redirects=False, auth=None)
    try:
        yield response
    finally:
        await response.aclose()


def create_relay(*, binding, token, admission, client, publish, enabled=False):
    """Dedicated private listener; deployment must isolate it from public routing.

    Credentials authenticate the owning server, not a browser. Only compiled
    operation names select destinations. No incoming headers reach the provider.
    The platform times its own HTTP exchange and never accepts reported values.
    """
    if not re.fullmatch(r"[a-f0-9]{64}", token):
        raise ValueError("Separate relay credential required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    active = 0

    @app.post("/v1/observe")
    async def observe(request: Request):
        nonlocal active
        if not enabled:
            return Response(status_code=404)
        if (not hmac.compare_digest(request.headers.get("authorization", "").encode(), ("Bearer " + token).encode())
                or request.headers.get("content-type") != "application/octet-stream"
                or request.headers.get("content-encoding") is not None):
            return Response(status_code=403)
        if active >= 8:
            return Response(status_code=429)
        active += 1
        try:
            raw = request.headers.get("x-experiment-attribution", "")
            if len(raw) > 512:
                return Response(status_code=400)
            try:
                a = Attribution.model_validate_json(raw)
                if not binding.permits(a) or a.action.int % binding.sample_every:
                    return Response(status_code=400)
                async with asyncio.timeout(3):
                    body = await _read(request.stream(), MAX_BODY)
                operation = binding.operations[a.operation]
                forwarded = session_headers(binding, operation, body, request.headers.get("x-experiment-session-bearer"))
                if operation.method == "GET" and body:
                    return Response(status_code=400)
            except (ValueError, TimeoutError):
                return Response(status_code=400)
            if not admission.take(a, int(time.time())):
                return Response(status_code=409)
            started = time.monotonic()
            status, content, content_type, etag = 502, b"", "application/octet-stream", None
            try:
                # Reserve 500ms inside the native deadline for the measured
                # timeout response and server observation to reach the caller.
                async with asyncio.timeout(operation.timeout_seconds - 0.5):
                    async with _forward(client, operation.method, operation.url + (a.target or ""), body,
                            {"content-type": "application/json", **operation.headers, **forwarded, "accept-encoding": "identity"}) as upstream:
                        if upstream.headers.get("content-encoding", "identity").lower() != "identity":
                            raise ValueError("Encoded operation refused")
                        content = await _read(upstream.aiter_bytes(), operation.response_limit)
                        status = upstream.status_code
                        etag = upstream.headers.get("etag")
                        content_type = upstream.headers.get("content-type", "application/octet-stream")
                        # Redirects do not silently change the measured dependency.
                        if 300 <= status < 400:
                            status, content, etag = 502, b"", None
            except (httpx.HTTPError, TimeoutError, ValueError):
                pass
            measured = Measurement(a, int(time.time()), (time.monotonic() - started) * 1000,
                                   status >= 500, operation.kind, status >= 400 and status not in (401, 403))
            try:
                async with asyncio.timeout(3):
                    await publish(measured)
            except Exception:
                pass
            return Response(content, status_code=status, headers={"Content-Type": content_type,
                "Cache-Control": "no-store", "X-Experiment-Action": str(a.action), **({"ETag": etag} if etag else {})})
        finally:
            active -= 1
    return app


class ServerObserver:
    """Called only after native assignment/session/consent checks, never reports.

    Null means use the ordinary path (disabled/unsampled). A relay failure raises;
    retry/fallback policy stays with the owning operation, especially for writes.
    """
    def __init__(self, *, binding, endpoint, token, client, publish, enabled=False):
        url = urlsplit(endpoint)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.path not in ("", "/")
                or not re.fullmatch(r"[a-f0-9]{64}", token)):
            raise ValueError("Private relay binding required")
        self.binding, self.url, self.token = binding, endpoint.rstrip("/") + "/v1/observe", token
        self.client, self.publish, self.enabled = client, publish, enabled

    async def request(self, *, experiment, variant, operation, body=b"", target=None, session_bearer=None):
        if not self.enabled:
            return None
        a = Attribution(action=uuid4(), experiment=experiment, variant=variant, operation=operation, issued=int(time.time()), target=target)
        if not self.binding.permits(a) or len(body) > MAX_BODY:
            raise ValueError("Unbound measured operation")
        spec = self.binding.operations[operation]
        session_headers(self.binding, spec, body, session_bearer)
        if spec.method == "GET" and body:
            raise ValueError("GET body refused")
        if a.action.int % self.binding.sample_every:
            return None
        started = time.monotonic()
        async with asyncio.timeout(spec.timeout_seconds):
            async with self.client.stream("POST", self.url, content=body, timeout=spec.timeout_seconds, follow_redirects=False,
                    headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/octet-stream",
                             "X-Experiment-Attribution": a.model_dump_json(),
                             **({"X-Experiment-Session-Bearer": session_bearer} if session_bearer is not None else {})}) as response:
                if response.headers.get("x-experiment-action") != str(a.action):
                    raise ValueError("Unobserved relay operation")
                if response.headers.get("content-encoding", "identity").lower() != "identity":
                    raise ValueError("Encoded relay response refused")
                content = await _read(response.aiter_bytes(), spec.response_limit)
                measured = Measurement(a, int(time.time()), (time.monotonic() - started) * 1000,
                                       response.status_code >= 500, self.binding.operations[operation].kind,
                                       response.status_code >= 400 and response.status_code not in (401, 403))
                result = httpx.Response(response.status_code, content=content,
                                        headers={"content-type": response.headers.get("content-type", "application/octet-stream"),
                                                 **({"etag": response.headers["etag"]} if "etag" in response.headers else {})})
        try:
            async with asyncio.timeout(3):
                await self.publish(measured)
        except Exception:
            pass
        return result


class BoundedPublisher:
    """Independent in-memory delivery, never the product analytics outbox.

    Full/stopped queues drop samples. No retries or disk content spool; missing
    observations cannot become a successful corroboration after recovery.
    """
    def __init__(self, publish):
        self.publish = publish
        self.queue = asyncio.Queue(maxsize=64)
        self.task = None

    async def __aenter__(self):
        self.task = asyncio.create_task(self._drain())
        return self

    async def __aexit__(self, *_):
        self.task.cancel()
        try:
            await self.task
        except asyncio.CancelledError:
            pass
        self.task = None

    async def __call__(self, measurement):
        if self.task is None or self.task.done():
            return False
        try:
            self.queue.put_nowait(measurement)
            return True
        except asyncio.QueueFull:
            return False

    async def _drain(self):
        while True:
            sample = await self.queue.get()
            try:
                if int(time.time()) - 60 <= sample.observed <= int(time.time()):
                    async with asyncio.timeout(3):
                        await self.publish(sample)
            except Exception:
                pass
            finally:
                self.queue.task_done()
