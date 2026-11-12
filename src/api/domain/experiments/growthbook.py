"""Authenticated, project/environment-bound GrowthBook import into Core policy.

SDK responses are not signatures. Only the dedicated private publisher reads
this source; clients continue verifying the existing signed cache contract.
"""
from dataclasses import dataclass
import json
import re
import secrets
from urllib.parse import quote, urlsplit
from uuid import UUID

import httpx

from api.domain.experiments.control import Configuration
from api.domain.experiments.publication import Publisher
from api.domain.experiments.store import ExperimentMissing


@dataclass(frozen=True)
class GrowthBookSource:
    origin: str
    organization: str
    project: str
    environment: str
    connection: str
    api_token: str
    sdk_key: str
    tenant: UUID
    audience: str
    maximum_stale_seconds: int = 300

    def __post_init__(self):
        parsed = urlsplit(self.origin)
        if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
                or parsed.password is not None or parsed.path or parsed.query or parsed.fragment):
            raise ValueError('GrowthBook HTTPS origin required')
        _ = parsed.port
        for value in (self.organization, self.project, self.environment, self.connection):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
                raise ValueError('Explicit GrowthBook binding required')
        for value in (self.api_token, self.sdk_key):
            if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,512}', value):
                raise ValueError('Separated GrowthBook read credentials required')
        if secrets.compare_digest(self.api_token, self.sdk_key):
            raise ValueError('Separated GrowthBook read credentials required')
        if (not isinstance(self.tenant, UUID) or self.tenant.int == 0
                or self.audience not in ('s3r.ch','hyperme.sh','portal')
                or type(self.maximum_stale_seconds) is not int or not 1 <= self.maximum_stale_seconds <= 300):
            raise ValueError('Bounded publisher binding required')

    async def read(self, client: httpx.AsyncClient) -> Configuration:
        async def get(path, authorization=None):
            headers = {'Accept':'application/json','Cache-Control':'no-cache'}
            if authorization: headers['Authorization'] = 'Bearer ' + authorization
            async with client.stream('GET', self.origin + path, headers=headers,
                                     follow_redirects=False, timeout=3) as response:
                if response.status_code != 200:
                    raise ValueError('GrowthBook source unavailable')
                raw = bytearray()
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    raw.extend(chunk)
                    if len(raw) > 65536: raise ValueError('GrowthBook source exceeds limit')
                value = json.loads(raw)
                if not isinstance(value, dict): raise ValueError('GrowthBook source unavailable')
                return value
        meta = (await get('/api/v1/sdk-connections/' + quote(self.connection, safe=''), self.api_token)).get('sdkConnection')
        if (not isinstance(meta, dict) or meta.get('id') != self.connection
                or meta.get('organization') != self.organization
                or meta.get('projects') != [self.project] or meta.get('project', self.project) != self.project
                or meta.get('environment') != self.environment
                or not isinstance(meta.get('key'),str) or not secrets.compare_digest(meta['key'], self.sdk_key)
                or meta.get('encryptPayload') is not False
                or any(meta.get(k,False) is not False for k in ('includeDraftExperiments','includeDraftExperimentRefs',
                    'includeVisualExperiments','includeRedirectExperiments','proxyEnabled','remoteEvalEnabled'))):
            raise ValueError('GrowthBook connection scope mismatch')
        payload = await get('/api/features/' + quote(self.sdk_key, safe=''))
        if payload.get('status',200) != 200 or set(payload.get('features',{})) != {'controlplane-policy'}:
            raise ValueError('Explicit GrowthBook policy required')
        feature = payload['features']['controlplane-policy']
        if (not isinstance(feature,dict) or feature.get('rules',[]) != []
                or set(feature) - {'defaultValue','rules','metadata'}):
            raise ValueError('Unconditional GrowthBook policy required')
        policy = feature.get('defaultValue')
        if (not isinstance(policy,dict) or set(policy) != {'version','tenant','audience','experiments'}
                or type(policy['version']) is not int or policy['version'] != 1
                or policy['tenant'] != str(self.tenant) or policy['audience'] != self.audience):
            raise ValueError('GrowthBook policy scope mismatch')
        return Configuration.model_validate_json(json.dumps({k:v for k,v in policy.items() if k!='version'} | {'sequence':1}))


async def import_and_publish(source: GrowthBookSource, client: httpx.AsyncClient,
                             publisher: Publisher, now: int) -> bool:
    if publisher.tenant != source.tenant or publisher.audience != source.audience:
        raise ValueError('GrowthBook publisher scope mismatch')
    # No load, write or renewal occurs unless this attempt authenticates both
    # the scoped connection and its current SDK policy over the configured TLS origin.
    incoming = await source.read(client)
    try:
        previous = await publisher.store.load(source.tenant, source.audience)
    except ExperimentMissing:
        await publisher.store.initialize(incoming)
    else:
        disabled = {e.key for e in previous.experiments if e.disabled}
        incoming_keys = {e.key for e in incoming.experiments}
        retained = tuple(e for e in previous.experiments if e.disabled and e.key not in incoming_keys)
        # Removed disabled entries remain tombstones. Re-adding an upstream key
        # cannot erase a controller decision. Validate the combined eight-key
        # bound before writing; an operator must explicitly clear old disables.
        updated = Configuration(tenant=incoming.tenant, audience=incoming.audience,
            sequence=previous.sequence + 1,
            experiments=tuple(e.model_copy(update={'disabled':True}) if e.key in disabled else e
                              for e in incoming.experiments) + retained)
        if previous.experiments != updated.experiments:
            if not await publisher.store.replace(previous,updated):
                raise ValueError('Experiment configuration changed; retry fresh import')
    return await publisher.publish(now, ttl=source.maximum_stale_seconds)
