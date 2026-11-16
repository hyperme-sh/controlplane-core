"""Explicit private HTTP composition; never mounted by default."""
import asyncio
import base64
import hmac
import json
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from .contracts import Refused, Scope, Subject

PRIVATE = {'Cache-Control':'private, no-store','Referrer-Policy':'no-referrer'}


def unique(pairs):
    result = dict(pairs)
    if len(result) != len(pairs): raise Refused('Invalid JSON')
    return result


async def body(request):
    if (request.headers.get('content-type','').split(';')[0] != 'application/json'
            or request.headers.get('content-encoding','identity') != 'identity'):
        raise Refused('Invalid content type')
    raw = bytearray()
    async for chunk in request.stream():
        if len(raw)+len(chunk)>4096: raise Refused('Request too large')
        raw.extend(chunk)
    return json.loads(raw, object_pairs_hook=unique)


def create_app(admissions, authenticate, callback_secret):
    """authenticate must resolve the real subject and tenant from owner authority.

    Use a separate private callback listener/ingress policy. Never trust forwarded
    IP headers or let a public reverse proxy reach the callback endpoint. The
    MediaMTX URL supplies the secret with HTTP Basic authentication.
    """
    if type(callback_secret) is not str or len(callback_secret)<32:
        raise Refused('Private callback credential required')
    expected = 'Basic ' + base64.b64encode(('mediamtx:'+callback_secret).encode()).decode()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    public_slots = asyncio.Semaphore(4)
    callback_slots = asyncio.Semaphore(4)
    pending_identities = set()

    @app.post('/streams/reserve')
    async def reserve(request: Request):
        identity = None
        try:
            if public_slots.locked(): raise Refused('Busy')
            async with public_slots, asyncio.timeout(0.8):
                subject = await authenticate(request)
                if type(subject) is not Subject: raise Refused('Authenticated identity required')
                if subject in pending_identities: raise Refused('Identity busy')
                # No await between membership and insertion: one event-loop task
                # owns this identity until completion, timeout or cancellation.
                pending_identities.add(subject)
                identity = subject
                value = await body(request)
                if type(value) is not dict or set(value) != {'room','action','seconds','fallback'} or value.get('fallback') is not True:
                    raise Refused('Invalid reservation')
                if request.client is None: raise Refused('Source unavailable')
                scope = Scope(subject,value['room'],value['action'],request.client.host)
                admission = admissions.get(subject.tenant)
                if admission is None: raise Refused('Tenant unavailable')
                return JSONResponse(await admission.issue(scope,value['seconds']), headers=PRIVATE)
        except Exception:
            return JSONResponse({'error':'stream-unavailable'},status_code=403,headers=PRIVATE)
        finally:
            if identity is not None: pending_identities.discard(identity)

    @app.post('/internal/mediamtx/auth')
    async def authorize(request: Request):
        try:
            supplied = request.headers.getlist('authorization')
            if (request.client is None or request.client.host not in ('127.0.0.1','::1')
                    or len(supplied)!=1 or not hmac.compare_digest(supplied[0],expected)
                    or callback_slots.locked()): raise Refused('Private callback required')
            async with callback_slots, asyncio.timeout(0.8):
                value = await body(request)
                from .contracts import callback
                from uuid import UUID
                callback(value)
                tenant = str(UUID(value['path'].split('/')[1]))
                admission = admissions.get(tenant)
                if admission is None: raise Refused('Tenant unavailable')
                return JSONResponse(await admission.authorize(value),headers=PRIVATE)
        except Exception:
            return JSONResponse({'error':'stream-unavailable'},status_code=403,headers=PRIVATE)
    return app
