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
    slots = asyncio.Semaphore(4)

    @app.post('/streams/reserve')
    async def reserve(request: Request):
        try:
            if slots.locked(): raise Refused('Busy')
            async with slots, asyncio.timeout(0.8):
                subject = await authenticate(request)
                if type(subject) is not Subject: raise Refused('Authenticated identity required')
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

    @app.post('/internal/mediamtx/auth')
    async def authorize(request: Request):
        try:
            supplied = request.headers.getlist('authorization')
            if (request.client is None or request.client.host not in ('127.0.0.1','::1')
                    or len(supplied)!=1 or not hmac.compare_digest(supplied[0],expected)
                    or slots.locked()): raise Refused('Private callback required')
            async with slots, asyncio.timeout(0.8):
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
