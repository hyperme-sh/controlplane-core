"""Fresh network-owner authorization for tenant storage configuration."""
from dataclasses import dataclass
import os
import json
import anyio
import re
import ssl
import time
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import HTTPException, Request

from api.domain.storage_scope import storage_tenant
from controlplane_identity.security.tenant_session_jwt import try_decode_tenant_session_jwt_async
from controlplane_identity.security.tenant_session_jwt import is_smoke_session


@dataclass(frozen=True)
class StorageAdmin:
    tenant: UUID
    subject: str


async def storage_admin(request: Request) -> StorageAdmin:
    auth = request.headers.get('authorization', '')
    if len(request.headers.getlist('authorization')) != 1 or not auth.startswith('Bearer ') or auth.count(' ') != 1 or len(auth) > 16391:
        raise HTTPException(401, 'Signed network session required')
    claims = await try_decode_tenant_session_jwt_async(auth[7:])
    try:
        if not claims or is_smoke_session(claims):
            raise ValueError()
        tenant = storage_tenant(claims.get('tenant_id'))
        subject = claims.get('sub')
        now = int(time.time())
        issued, expires = claims.get('iat'), claims.get('exp')
        if (str(tenant) != claims.get('tenant_id') or type(claims.get('scope_version')) is not int or claims['scope_version'] != 1
                or claims.get('scope_kind') != 'network' or claims.get('organization_id') is not None
                or not isinstance(subject, str) or str(UUID(subject)) != subject
                or type(issued) is not int or type(expires) is not int or not 0 <= issued <= now < expires or expires - issued > 900
                or not isinstance(claims.get('network_id'), str) or not 1 <= len(claims['network_id']) <= 255
                or not isinstance(claims.get('scope_generation'), str) or not 1 <= len(claims['scope_generation']) <= 128
                or not isinstance(claims.get('scope_manifest_sha256'), str) or not re.fullmatch('[a-f0-9]{64}', claims['scope_manifest_sha256'])
                or request.headers.getlist('x-tenant-id') != [str(tenant)]):
            raise ValueError()
    except (TypeError, ValueError, AttributeError):
        raise HTTPException(403, 'Exact signed tenant and network scope required') from None
    base = os.environ.get('USER_SERVICE_URL', '').rstrip('/')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path or parsed.query or parsed.fragment:
        raise HTTPException(503, 'Storage authority unavailable')
    body = {'version': 1, 'request_id': str(uuid4()), 'action': 'network.admin',
        'network': claims['network_id'], 'tenant_id': None, 'organization_id': None}
    try:
        context = ssl.create_default_context(cafile=os.environ.get('USER_SERVICE_CA_FILE') or None)
        with anyio.fail_after(3):
            async with httpx.AsyncClient(timeout=3, trust_env=False, follow_redirects=False, verify=context) as client:
                async with client.stream('POST', base + '/api/v1/authority/check',
                        headers={'Authorization': auth, 'X-Tenant-ID': str(tenant)}, json=body) as response:
                    if response.status_code in (401, 403):
                        raise HTTPException(403, 'Current storage owner authority required')
                    if response.status_code != 200 or 'no-store' not in response.headers.get('cache-control', '').replace(' ', '').split(','):
                        raise ValueError()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes(chunk_size=4096):
                        raw.extend(chunk)
                        if len(raw) > 16384:
                            raise ValueError()
                    result = json.loads(raw)
                    if not isinstance(result, dict) or any(result.get(k) != v for k, v in body.items()) or result.get('subject') != subject:
                        raise ValueError()
                    if result.get('allowed') is not True:
                        raise HTTPException(403, 'Current storage owner authority required')
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, 'Storage authority unavailable') from exc
    return StorageAdmin(tenant, subject)
