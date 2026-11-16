"""Nonce-bound transport to a protected identity/current-room Check owner."""
import asyncio
from dataclasses import asdict
import base64
import json
import secrets
import time
from urllib.parse import urlsplit
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from .contracts import AUTHORITY_TIMEOUT, Decision, Refused, Scope, Subject

DOMAIN = b'core-stream-authority-v1\0'


def canonical(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True,allow_nan=False).encode('ascii')


class ProtectedAuthority:
    """Only a configured owner can resolve identity or attest a fresh room Check.

    The configured endpoint must use the native authenticated identity and room
    Check, not trust request principal labels. Its protocol is below; Core never
    caches an allow decision or silently falls back to a public graph.
    """
    def __init__(self, client, endpoint, service_key, public_key, *, clock=time.time):
        parsed = urlsplit(endpoint)
        if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or not parsed.path.startswith('/')
                or type(service_key) is not str or not 32<=len(service_key)<=512
                or not isinstance(public_key,Ed25519PublicKey)):
            raise Refused('Protected authority configuration required')
        self.client,self.endpoint,self.service_key,self.public_key,self.clock = client,endpoint,service_key,public_key,clock

    async def call(self, operation, value, bearer=None):
        from .http import unique
        started = int(self.clock())
        nonce = secrets.token_hex(32)
        request = {'version':1,'nonce':nonce,'operation':operation,**value}
        headers = {'X-Stream-Service-Key':self.service_key}
        if bearer is not None: headers['Authorization'] = bearer
        async with asyncio.timeout(AUTHORITY_TIMEOUT):
            async with self.client.stream('POST',self.endpoint,json=request,headers=headers,follow_redirects=False) as response:
                if response.status_code!=200: raise Refused('Protected authority unavailable')
                raw=bytearray()
                async for part in response.aiter_bytes():
                    if len(raw)+len(part)>4096: raise Refused('Authority response too large')
                    raw.extend(part)
        try:
            envelope=json.loads(raw,object_pairs_hook=unique)
            if type(envelope) is not dict or set(envelope)!={'claims','signature'}: raise ValueError()
            claims=envelope['claims']
            self.public_key.verify(base64.b64decode(envelope['signature'],validate=True),DOMAIN+canonical(claims))
            finished = int(self.clock())
            if (claims['nonce']!=nonce or type(claims['version']) is not int or claims['version']!=1
                    or claims['operation']!=operation or type(claims['evaluatedAt']) is not int
                    or not 0<=finished-started<=1
                    or not started<=claims['evaluatedAt']<=finished): raise ValueError()
            return claims
        except Exception:
            raise Refused('Invalid protected authority response') from None

    async def authenticate(self, request):
        values=request.headers.getlist('authorization')
        if len(values)!=1 or not values[0].startswith('Bearer ') or not 8<len(values[0])<=8192:
            raise Refused('Authenticated stream identity required')
        claims=await self.call('identity',{},values[0])
        if set(claims)!={'version','nonce','operation','evaluatedAt','tenant','principal'}:
            raise Refused('Invalid authenticated identity')
        return Subject(claims['tenant'],claims['principal'])

    async def check(self, scope):
        claims=await self.call('check',{'scope':asdict(scope)})
        if set(claims)!={'version','nonce','operation','evaluatedAt','scope','allowed','validUntil','revision'}:
            raise Refused('Invalid current room Check')
        if claims['scope']!=asdict(scope): raise Refused('Room Check scope mismatch')
        return Decision(scope,claims['allowed'],claims['validUntil'],claims['revision'])
