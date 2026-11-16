"""Explicit Linux relay process composition; absent/disabled configuration does nothing."""
import asyncio
import base64
from contextlib import asynccontextmanager
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx
import psycopg
import uvicorn
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from api.storage.configure_provider import private_json
from .admission import Admission
from .authority import ProtectedAuthority
from .contracts import Refused, uuid
from .controller import Controller
from .http import create_app
from .mediamtx import MediaMTX
from .postgres import PostgresStreams
from .watchdog import heartbeat


FIELDS={'version','enabled','tenant','generation','database','schema','authorityUrl','authorityKey',
        'serviceKey','callbackSecret','listenPort','tlsKey','tlsCertificate','mediaConfig','publicHost','webOrigin'}


def configuration(path):
    value=private_json(path)
    if (type(value.get('version')) is not int or value['version']!=1
            or value.get('enabled') is not True or set(value)!=FIELDS):
        raise Refused('Explicit private stream configuration required')
    uuid(value['tenant']);uuid(value['generation'])
    if type(value['listenPort']) is not int or not 1024<=value['listenPort']<=65535:
        raise Refused('Invalid stream port')
    # Secret-bearing config is private_json-protected; no environment/default
    # credential search, generated keys, provider changes or schema bootstrap.
    for name in ('tlsKey','tlsCertificate','mediaConfig'):
        path=Path(value[name])
        if not path.is_absolute() or path.resolve()!=path or not path.is_file():
            raise Refused('Existing stream material required')
    native=private_json(value['mediaConfig'])
    from .native import configuration as native_configuration
    if native!=native_configuration(value):
        raise Refused('Private media policy required')
    return value


async def run(value):
    @asynccontextmanager
    async def connect():
        async with await psycopg.AsyncConnection.connect(value['database'],connect_timeout=1) as connection:
            yield connection
    store=PostgresStreams(value['tenant'],connect,schema=value['schema'])
    # One controller per tenant, including across process restarts. Session locks
    # are released by PostgreSQL on disconnect, not by caller-owned flags.
    lock=int.from_bytes(hashlib.sha256(value['tenant'].encode()).digest()[:4],'big',signed=True)
    async with await psycopg.AsyncConnection.connect(value['database'],autocommit=True,connect_timeout=1) as lease:
        row=await (await lease.execute('SELECT pg_try_advisory_lock(131177,%s)',(lock,))).fetchone()
        if row!=(True,): raise Refused('Stream controller already owned')
        # Let any disconnected predecessor's independent watchdog expire before
        # reclaiming its rows. Daily reserved bytes are never refunded.
        await asyncio.sleep(1.1)
        rows=await store.outstanding(value['generation'])
        for row in rows: await store.close(row['reservation_id'])
        async with httpx.AsyncClient(timeout=0.25,trust_env=False,limits=httpx.Limits(max_connections=32)) as client:
            authority=ProtectedAuthority(client,value['authorityUrl'],value['serviceKey'],
                Ed25519PublicKey.from_public_bytes(base64.b64decode(value['authorityKey'],validate=True)))
            admission=Admission(store,authority,value['generation'])
            app=create_app({value['tenant']:admission},authority.authenticate,value['callbackSecret'])
            server=uvicorn.Server(uvicorn.Config(app,host='0.0.0.0',port=value['listenPort'],
                ssl_keyfile=value['tlsKey'],ssl_certfile=value['tlsCertificate'],
                access_log=False,log_level='critical',proxy_headers=False))
            serving=asyncio.create_task(server.serve())
            read_fd,write_fd=os.pipe()
            guard=None
            try:
                async with asyncio.timeout(5):
                    while not server.started:
                        if serving.done(): raise Refused('Stream listener unavailable')
                        await asyncio.sleep(0.02)
                guard=subprocess.Popen([sys.executable,str(Path(__file__).with_name('watchdog.py')),str(read_fd),value['mediaConfig']],
                    pass_fds=(read_fd,),stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                os.close(read_fd);read_fd=None
                await asyncio.sleep(0.15)
                controller=Controller(admission,MediaMTX(client,'http://127.0.0.1:9997'),lambda:heartbeat(write_fd))
                while guard.poll() is None and not serving.done():
                    async with asyncio.timeout(0.8):
                        await lease.execute('SELECT 1')
                        await controller.tick()
                    await asyncio.sleep(0.1)
                raise Refused('Stream process stopped')
            finally:
                os.close(write_fd)
                if read_fd is not None:os.close(read_fd)
                if guard is not None:
                    await asyncio.to_thread(guard.wait,2)
                server.should_exit=True
                try:await asyncio.wait_for(serving,2)
                except TimeoutError:serving.cancel()


if __name__=='__main__':
    try:
        if len(sys.argv)!=2:raise Refused('Private configuration required')
        asyncio.run(run(configuration(sys.argv[1])))
    except Exception:
        raise SystemExit('Stream service unavailable; no private details reported') from None
