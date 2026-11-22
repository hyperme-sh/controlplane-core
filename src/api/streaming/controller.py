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
        self.negotiating = {}

    async def tick(self):
        async with asyncio.timeout(0.8):
            # Read native state before the ledger so an already-established
            # session cannot be missed by an earlier database snapshot. Native
            # pre-auth negotiations are handled separately below.
            sessions = await self.media.sessions()
            rows = await self.admission.store.outstanding(self.admission.generation)
            known = {str(row['media_session']) for row in rows if row['phase']==1}
            now,stamp = int(self.clock()),self.monotonic()

            unknown = set(sessions) - known
            negotiating = {(sessions[sid].get('path'), sessions[sid].get('state')) for sid in unknown}
            active_publishers = [
                row for row in rows if row['phase'] == 1 and row['action'] == 'publish'
                and str(row['media_session']) in sessions
            ]

            def expired(row):
                return (str(row['generation']) != self.admission.generation or row['expires_at'] <= now
                        or row['phase'] == 0 and row['issued_at'] + 10 <= now)

            async def current(row):
                if expired(row):
                    return False
                # Idle reservations have no media to renew. The callback
                # performs its own fresh Check before consuming the grant.
                if row['phase'] == 0 and (row['path'], row['action']) not in negotiating:
                    return False
                try:
                    await self.admission.check(self.admission.store.scope(row))
                    return True
                except Exception: return False
            # MediaMTX serializes path listings behind publisher/reader auth.
            # Read paths alongside current authority, within the same finite
            # tick, instead of stacking both native waits. Session-before-row
            # ordering above remains mandatory for tracking new admissions.
            async with asyncio.TaskGroup() as checks:
                paths_task = checks.create_task(self.media.paths()) if active_publishers else None
                current_tasks = [checks.create_task(current(row)) for row in rows]
            paths = paths_task.result() if paths_task else {}
            valid = [task.result() for task in current_tasks]
            # Native sessions become visible before their auth callback commits.
            # Only a zero-byte, unconnected negotiation matching a currently
            # allowed pending reservation may wait for that commit, briefly.
            for sid in unknown:
                native = sessions[sid]
                pending = any(allowed and row['phase']==0
                              and row['path']==native.get('path')
                              and row['action']==native.get('state')
                              for row,allowed in zip(rows,valid))
                first = self.negotiating.setdefault(sid,stamp)
                if (not pending or native.get('peerConnectionEstablished') is not False
                        or any(type(native.get(key)) is not int or native[key]!=0
                               for key in ('inboundBytes','outboundBytes'))
                        or self.monotonic()-first>=0.7):
                    raise Refused('Untracked media session')
            self.negotiating = {sid:at for sid,at in self.negotiating.items() if sid in unknown}
            publishers = {row['path'] for row,allowed in zip(rows,valid)
                          if allowed and row['action']=='publish' and row['phase']==1
                          and str(row['media_session']) in sessions and compatible_path(paths.get(row['path']))}
            for row,allowed in zip(rows,valid):
                if row['phase'] == 0:
                    if expired(row):
                        await self.admission.store.close(row['reservation_id'], pending_only=True)
                    continue
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
