"""Inactive owner probe for bound Storage targets; no fixture provisioning.

The deployment owner supplies separate authenticated baseline/candidate routes
and a declared synthetic tenant. No CLI, scheduler or activation is installed.
"""
from dataclasses import dataclass, field
import json
import re
import time
from urllib.parse import urlsplit
from uuid import UUID

import anyio
import httpx


class CanaryRefused(RuntimeError):
    pass


def require(value, message):
    if not value:
        raise CanaryRefused(message)


@dataclass(frozen=True)
class Run:
    tenant: UUID
    identity: UUID
    expires_at: int
    created_at: int

    def __post_init__(self):
        require(type(self.tenant) is UUID and self.tenant.int and type(self.identity) is UUID
                and self.identity.int, 'Declared tenant and run identity required')
        require(type(self.created_at) is int and type(self.expires_at) is int
                and 0 < self.expires_at - self.created_at <= 300, 'Bounded run lifetime required')

    @property
    def path(self):
        return '/api/v1/storage/records/fixtures/canary_' + self.identity.hex

    def body(self, generation):
        return json.dumps({'kind': 'storage-canary-v1', 'tenant': str(self.tenant),
            'run': str(self.identity), 'created_at': self.created_at,
            'expires_at': self.expires_at, 'generation': generation},
            sort_keys=True, separators=(',', ':')).encode()

    def active(self, now):
        require(self.created_at <= now < self.expires_at, 'Canary run expired or not started')


@dataclass
class Target:
    origin: str
    revision: str
    token: str = field(repr=False)
    enabled: bool = False
    transport: object = field(default=None, repr=False)

    def __post_init__(self):
        url = urlsplit(self.origin)
        require(url.scheme == 'https' and url.hostname and not url.username and not url.password
                and not url.query and not url.fragment and url.path == '', 'Exact HTTPS target required')
        require(isinstance(self.revision, str) and re.fullmatch(r'[a-f0-9]{40}', self.revision), 'Exact source revision required')
        require(isinstance(self.token, str) and 1 <= len(self.token) <= 16384
                and not any(c.isspace() for c in self.token), 'Separate scoped credential required')

    async def request(self, method, path, *, run=None, data=None, headers=None, authenticated=True):
        require(self.enabled is True, 'Canary binding disabled')
        require(path == '/version' or (run is not None and path == run.path), 'Canary path refused')
        selected = dict(headers or {})
        if authenticated:
            selected['Authorization'] = 'Bearer ' + self.token
        if run is not None:
            selected['X-Tenant-Id'] = str(run.tenant)
        async with httpx.AsyncClient(base_url=self.origin, transport=self.transport,
                timeout=5, follow_redirects=False, trust_env=False) as client:
            with anyio.fail_after(8):
                async with client.stream(method, path, content=data, headers=selected) as reply:
                    body = bytearray()
                    async for chunk in reply.aiter_bytes():
                        require(len(body) + len(chunk) <= 4096, 'Oversized canary response')
                        body.extend(chunk)
                    return reply.status_code, dict(reply.headers), bytes(body)

    async def identify(self):
        code, _, body = await self.request('GET', '/version', authenticated=False)
        require(code == 200, 'Storage revision unavailable')
        try:
            value = json.loads(body)
        except (ValueError, TypeError):
            raise CanaryRefused('Invalid Storage revision') from None
        require(isinstance(value, dict) and value.get('service') == 'tenant-storage'
                and value.get('git_sha') == self.revision, 'Wrong Storage target')


def revision(headers):
    value = headers.get('etag', '')
    require(re.fullmatch(r'"[a-f0-9]{64}"', value), 'Record revision unavailable')
    return value


async def cleanup(target, run):
    """Independent, repeatable cleanup, including after expiry or process loss.

    The caller must retain the exact run descriptor before the first write.
    Never list a tenant, force-delete a revision, or remove another run's bytes.
    """
    await target.identify()
    code, headers, body = await target.request('GET', run.path, run=run)
    if code == 404:
        return {'cleanup': 'absent'}
    require(code == 200 and body in (run.body(0), run.body(1)), 'Foreign or changed canary record')
    code, _, _ = await target.request('DELETE', run.path, run=run, headers={'If-Match': revision(headers)})
    require(code == 204, 'Conditional cleanup refused')
    code, _, _ = await target.request('GET', run.path, run=run)
    require(code == 404, 'Canary cleanup not confirmed')
    await target.identify()
    return {'cleanup': 'absent'}


async def exercise(baseline, candidate, run, *, clock=time.time):
    """Cross-target CRUD/rollback contract; target routing is an Infra binding."""
    run.active(clock())
    await baseline.identify(); await candidate.identify()
    marks = []

    async def call(target, method, *, data=None, headers=None, authenticated=True):
        run.active(clock())
        await target.identify()
        reply = await target.request(method, run.path, run=run, data=data,
                                     headers=headers, authenticated=authenticated)
        await target.identify()
        run.active(clock())
        return reply

    binary = {'Content-Type': 'application/octet-stream'}
    for target in (baseline, candidate):
        code, _, _ = await call(target, 'GET', authenticated=False)
        require(code in (401, 403), 'Unauthenticated record access accepted')
    marks.append('authority-refused')
    # Persist Run before invoking; cleanup can recover a lost create reply.
    try:
        code, headers, _ = await call(baseline, 'PUT', data=run.body(0),
                                      headers=binary | {'If-None-Match': '*'})
        require(code == 201, 'Synthetic run already exists or create failed')
        first = revision(headers)
        code, headers, body = await call(candidate, 'GET')
        require(code == 200 and body == run.body(0) and revision(headers) == first, 'Candidate cannot read baseline')
        marks.append('baseline-create-candidate-read')
        code, headers, _ = await call(candidate, 'PUT', data=run.body(1), headers=binary | {'If-Match': first})
        require(code == 204, 'Candidate conditional update failed')
        second = revision(headers)
        require(first != second, 'Record revision did not advance')
        code, headers, body = await call(baseline, 'GET')
        require(code == 200 and body == run.body(1) and revision(headers) == second, 'Baseline cannot read candidate')
        marks.append('candidate-update-baseline-read')
        code, _, _ = await call(baseline, 'PUT', data=run.body(0), headers=binary | {'If-Match': first})
        require(code == 412, 'Stale write accepted')
        code, _, _ = await call(candidate, 'DELETE', headers={'If-Match': first})
        require(code == 412, 'Stale delete accepted')
        marks.append('stale-mutations-refused')
    finally:
        await cleanup(baseline, run)
    return {'baseline': baseline.revision, 'candidate': candidate.revision,
            'marks': marks + ['cleanup-absent']}
