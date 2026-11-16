"""Uninstalled transport for the owning service's native identity and room Check."""
import asyncio
import base64
from dataclasses import asdict
import hmac
import re
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from .authority import DOMAIN, canonical
from .contracts import Decision, Refused, Scope, Subject
from .http import PRIVATE, body


def create_app(source, signing_key, service_key, *, clock=time.time):
    """source.authenticate(request) and source.check(scope) are native owner calls.

    Bind this factory within that owner's private process, with its protected
    source/checkpoint and authenticated session resolver. No public-graph or
    caller-claim implementation is supplied as a default.
    """
    if (getattr(source,'production_ready',False) is not True
            or not isinstance(signing_key,Ed25519PrivateKey) or type(service_key) is not str
            or not 32<=len(service_key)<=512):raise Refused('Protected owner binding required')
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    slots=asyncio.Semaphore(32)

    @app.post('/internal/streams/authority')
    async def authority(request:Request):
        try:
            values=request.headers.getlist('x-stream-service-key')
            if len(values)!=1 or not hmac.compare_digest(values[0],service_key) or slots.locked():
                raise Refused('Private owner caller required')
            async with slots,asyncio.timeout(0.2):
                value=await body(request)
                if (type(value) is not dict or type(value.get('version')) is not int or value['version']!=1
                        or type(value.get('nonce')) is not str or not re.fullmatch('[a-f0-9]{64}',value['nonce'])):
                    raise Refused('Invalid owner request')
                operation=value.get('operation')
                if operation=='identity' and set(value)=={'version','nonce','operation'}:
                    subject=await source.authenticate(request)
                    if type(subject) is not Subject:raise Refused('Owner identity unavailable')
                    result=asdict(subject)
                elif operation=='check' and set(value)=={'version','nonce','operation','scope'}:
                    scope=value['scope']
                    if type(scope) is not dict or set(scope)!={'subject','room','action','ip'}:raise Refused('Invalid scope')
                    subject=scope['subject']
                    if type(subject) is not dict or set(subject)!={'tenant','principal'}:raise Refused('Invalid subject')
                    scope=Scope(Subject(**subject),scope['room'],scope['action'],scope['ip'])
                    decision=await source.check(scope)
                    if type(decision) is not Decision:raise Refused('Owner Check unavailable')
                    decision.validate(scope,int(clock()))
                    result={'scope':asdict(scope),'allowed':True,'validUntil':decision.valid_until,'revision':decision.revision}
                else:raise Refused('Invalid owner operation')
                claims={'version':1,'nonce':value['nonce'],'operation':operation,'evaluatedAt':int(clock()),**result}
                return JSONResponse({'claims':claims,'signature':base64.b64encode(signing_key.sign(DOMAIN+canonical(claims))).decode()},headers=PRIVATE)
        except Exception:
            return JSONResponse({'error':'stream-authority-unavailable'},status_code=403,headers=PRIVATE)
    return app
