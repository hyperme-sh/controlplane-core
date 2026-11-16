"""Bounded current-authority checks and active WebRTC session teardown."""
import asyncio
import time
from .contracts import BYTES_PER_SECOND, POLL_SECONDS, Refused
from .mediamtx import compatible_path


class Controller:
    def __init__(self, admission, media, renew, *, clock=time.time, monotonic=time.monotonic):
        self.admission,self.media,self.renew,self.clock,self.monotonic = admission,media,renew,clock,monotonic
        self.waiting = {}
        self.counters = {}
        self.meters = {}

    async def tick(self):
        async with asyncio.timeout(0.8):
            # Read native state first: auth commits its DB row before a session
            # appears here, so a later DB snapshot cannot miss a new admission.
            sessions = await self.media.sessions()
            paths = await self.media.paths()
            rows = await self.admission.store.outstanding(self.admission.generation)
            known = {str(row['media_session']) for row in rows if row['phase']==1}
            if set(sessions)-known: raise Refused('Untracked media session')
            now,stamp = int(self.clock()),self.monotonic()

            async def current(row):
                if (str(row['generation'])!=self.admission.generation or row['expires_at']<=now
                        or row['phase']==0 and row['issued_at']+10<=now): return False
                try:
                    await self.admission.check(self.admission.store.scope(row))
                    return True
                except Exception: return False
            valid = await asyncio.gather(*(current(row) for row in rows))
            publishers = {row['path'] for row,allowed in zip(rows,valid)
                          if allowed and row['action']=='publish' and row['phase']==1
                          and str(row['media_session']) in sessions and compatible_path(paths.get(row['path']))}
            for row,allowed in zip(rows,valid):
                sid = str(row['media_session']) if row['media_session'] else None
                if row['action']=='read' and row['path'] not in publishers: allowed=False
                if sid:
                    native=sessions.get(sid)
                    if native is None:
                        first=self.waiting.setdefault(sid,stamp)
                        if stamp-first>=0.5: allowed=False
                    else:
                        self.waiting.pop(sid,None)
                        if native.get('path')!=row['path'] or native.get('state')!=row['action']:
                            raise Refused('Native stream mapping mismatch')
                        counter=native.get('inboundBytes' if row['action']=='publish' else 'outboundBytes')
                        if type(counter) is not int or counter<self.counters.get(sid,0):
                            raise Refused('Native stream counter unavailable')
                        previous=self.counters.get(sid,0)
                        at,credit=self.meters.get(sid,(stamp-min(1,max(0,self.clock()-row['issued_at'])),65536.0))
                        credit=min(65536.0,credit+max(0,stamp-at)*BYTES_PER_SECOND-(counter-previous))
                        self.meters[sid]=(stamp,credit)
                        if credit<0:allowed=False
                        self.counters[sid]=counter
                        if row['action']=='publish' and row['path'] not in publishers:
                            first=self.waiting.setdefault('path:'+sid,stamp)
                            if stamp-first>=0.5:allowed=False
                        else:self.waiting.pop('path:'+sid,None)
                        if counter>=row['reserved_bytes']: allowed=False
                if not allowed:
                    if sid in sessions: await self.media.kick(sid)
                    await self.admission.store.close(row['reservation_id'])
            retained={str(row['media_session']) for row in rows if row['media_session']}
            self.counters={k:v for k,v in self.counters.items() if k in retained}
            self.waiting={k:v for k,v in self.waiting.items() if k in retained or k.removeprefix('path:') in retained}
            self.meters={k:v for k,v in self.meters.items() if k in retained}
            self.renew()

    async def run(self):
        while True:
            await self.tick()
            await asyncio.sleep(POLL_SECONDS)
