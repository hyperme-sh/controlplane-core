"""Offline stream admission contracts; in-memory doubles never qualify a server."""
import asyncio
from dataclasses import asdict
import hashlib
from uuid import uuid4
import pytest
from api.streaming.admission import Admission
from api.streaming.contracts import Decision, Refused, Scope, Subject, callback
from api.streaming.controller import Controller

TENANT='11111111-1111-4111-8111-111111111111'
GENERATION='22222222-2222-4222-8222-222222222222'
SCOPE=Scope(Subject(TENANT,'protected-principal'),'room','publish','192.0.2.1')
NOW=1000


class Authority:
    allowed=True
    delay=0
    async def check(self,scope):
        await asyncio.sleep(self.delay)
        return Decision(scope,self.allowed,2000,'a'*64)


class Store:
    def __init__(self):self.rows=[]
    async def reserve(self,scope,generation,reservation,token_hash,now,deadline):
        row={'tenant_id':scope.subject.tenant,'principal':scope.subject.principal,'room':scope.room,
             'action':scope.action,'source_ip':scope.ip,'generation':generation,'reservation_id':reservation,
             'token_hash':token_hash,'issued_at':now,'expires_at':deadline,'reserved_bytes':(deadline-now)*391000,
             'path':'stream/'+TENANT.replace('-','')+'/'+uuid4().hex,'phase':0,'media_session':None}
        self.rows.append(row);return row
    async def lookup(self,path,token_hash,generation):
        for row in self.rows:
            if row['path']==path and row['token_hash']==token_hash and row['generation']==generation and row['phase']==0:return dict(row)
        raise Refused('Missing')
    async def consume(self,row,session,now):
        found=next(r for r in self.rows if r['reservation_id']==row['reservation_id'])
        if found['phase']!=0 or found['expires_at']<=now or found['issued_at']<now-10:raise Refused('Consumed')
        found.update(phase=1,media_session=session)
    def scope(self,row):return Scope(Subject(row['tenant_id'],row['principal']),row['room'],row['action'],row['source_ip'])
    async def outstanding(self,generation):return [dict(r) for r in self.rows if r['phase']!=2]
    async def close(self, reservation, *, pending_only=False):
        for row in self.rows:
            if row['reservation_id'] == reservation and (not pending_only or row['phase'] == 0):
                row['phase'] = 2


def wire(grant,**changes):
    return {'ip':SCOPE.ip,'user':'','password':'','token':grant['token'],'action':'publish',
            'path':grant['path'],'protocol':'webrtc','id':str(uuid4()),'query':'','userAgent':'contract',**changes}


@pytest.mark.asyncio
async def test_token_is_hashed_bound_once_and_expiry_is_finite():
    store=Store();service=Admission(store,Authority(),GENERATION,clock=lambda:NOW)
    grant=await service.issue(SCOPE,60)
    assert store.rows[0]['token_hash']==hashlib.sha256(grant['token'].encode()).hexdigest()
    for changes in ({'ip':'192.0.2.2'},{'action':'read'},{'path':grant['path'][:-1]+'z'}, {'protocol':'rtsp'}, {'query':'token=secret'}):
        with pytest.raises(Refused):await service.authorize(wire(grant,**changes))
    request=wire(grant)
    assert (await service.authorize(request))['expireAt']=='1970-01-01T00:17:40Z'
    with pytest.raises(Refused):await service.authorize(request)
    with pytest.raises(Refused):await service.authorize(wire(grant))


@pytest.mark.asyncio
async def test_current_authority_is_required_twice_and_time_is_bounded():
    authority=Authority();store=Store();service=Admission(store,authority,GENERATION,clock=lambda:NOW)
    grant=await service.issue(SCOPE,60);authority.allowed=False
    with pytest.raises(Refused):await service.authorize(wire(grant))
    assert store.rows[0]['phase']==0
    with pytest.raises(Refused):await service.issue(SCOPE,60)
    authority.delay=1
    with pytest.raises(TimeoutError):await service.issue(SCOPE,60)


@pytest.mark.asyncio
async def test_midnight_and_owner_deadline_shorten_sessions():
    class Midnight:
        async def check(self,scope):return Decision(scope,True,89999,'a'*64)
    service=Admission(Store(),Midnight(),GENERATION,clock=lambda:86399)
    assert (await service.issue(SCOPE,3600))['expiresAt']==86400
    for value in (True,0,3601,'60'):
        with pytest.raises(Refused):await service.issue(SCOPE,value)


class Media:
    def __init__(self):self.values={};self.kicked=[]
    async def sessions(self):return dict(self.values)
    async def paths(self):
        return {row['path']:{'online':True,'tracks2':[{'codec':'H264','codecProps':{'profile':'Baseline'}},{'codec':'Opus','codecProps':{'channelCount':2}}]} for row in self.values.values() if 'path' in row}
    async def kick(self,sid):self.kicked.append(sid);self.values.pop(sid)


@pytest.mark.asyncio
async def test_controller_revocation_kicks_before_release_and_renews_only_complete_pass():
    authority=Authority();store=Store();service=Admission(store,authority,GENERATION,clock=lambda:NOW)
    grant=await service.issue(SCOPE,60);request=wire(grant);await service.authorize(request)
    media=Media();media.values[request['id']]={'path':grant['path'],'state':'publish','inboundBytes':1}
    renew=[];control=Controller(service,media,lambda:renew.append(True),clock=lambda:NOW)
    await control.tick();assert renew==[True] and store.rows[0]['phase']==1
    authority.allowed=False
    await control.tick();assert media.kicked==[request['id']] and store.rows[0]['phase']==2
    media.values[str(uuid4())]={}
    with pytest.raises(Refused):await control.tick()
    assert len(renew)==2


@pytest.mark.asyncio
async def test_counter_regression_and_native_scope_mismatch_stop_renewal():
    for mutate in (lambda row:row.update(path='foreign'),lambda row:row.update(inboundBytes=-1)):
        store=Store();service=Admission(store,Authority(),GENERATION,clock=lambda:NOW)
        grant=await service.issue(SCOPE,60);request=wire(grant);await service.authorize(request)
        native={'path':grant['path'],'state':'publish','inboundBytes':0};mutate(native)
        media=Media();media.values[request['id']]=native
        control=Controller(service,media,lambda:pytest.fail('Must not renew'),clock=lambda:NOW)
        with pytest.raises(Refused):await control.tick()


@pytest.mark.asyncio
async def test_budget_exhaustion_closes_native_session():
    store=Store();service=Admission(store,Authority(),GENERATION,clock=lambda:NOW)
    grant=await service.issue(SCOPE,1);request=wire(grant);await service.authorize(request)
    media=Media();media.values[request['id']]={'path':grant['path'],'state':'publish','inboundBytes':391000}
    await Controller(service,media,lambda:None,clock=lambda:NOW).tick()
    assert media.kicked==[request['id']]


@pytest.mark.asyncio
async def test_native_path_and_authority_waits_overlap_without_renewing_early():
    entered=asyncio.Event();release=asyncio.Event();finished=[]
    class ConcurrentAuthority(Authority):
        async def check(self,scope):
            entered.set()
            await release.wait()
            finished.append('authority')
            return await super().check(scope)
    class ConcurrentMedia(Media):
        async def paths(self):
            await entered.wait()
            # Native media can legitimately take this long behind admission.
            await asyncio.sleep(0.3)
            release.set()
            finished.append('paths')
            return await super().paths()
    store=Store();service=Admission(store,Authority(),GENERATION,clock=lambda:NOW)
    grant=await service.issue(SCOPE,60);request=wire(grant);await service.authorize(request)
    service.authority=ConcurrentAuthority()
    media=ConcurrentMedia();media.values[request['id']]={'path':grant['path'],'state':'publish','inboundBytes':1}
    renew=[]
    def renewed():
        assert set(finished)=={'authority','paths'}
        renew.append(True)
    await Controller(service,media,renewed,clock=lambda:NOW).tick()
    assert renew==[True] and store.rows[0]['phase']==1


@pytest.mark.asyncio
async def test_visible_negotiation_must_match_allowed_pending_row_and_stay_empty():
    store=Store();authority=Authority();service=Admission(store,authority,GENERATION,clock=lambda:NOW)
    grant=await service.issue(SCOPE,60);sid=str(uuid4())
    native={'path':grant['path'],'state':'publish','peerConnectionEstablished':False,
            'inboundBytes':0,'outboundBytes':0}
    media=Media();media.values[sid]=native.copy();renew=[];stamp=[0.]
    control=Controller(service,media,lambda:renew.append(True),clock=lambda:NOW,monotonic=lambda:stamp[0])
    await control.tick();assert renew==[True]
    for mutation in ({'peerConnectionEstablished':True},{'inboundBytes':1},
                     {'outboundBytes':True},{'path':'foreign'},{'state':'read'}):
        media.values[sid]=native|mutation
        with pytest.raises(Refused):await control.tick()
    media.values[sid]=native.copy();authority.allowed=False
    with pytest.raises(Refused):await control.tick()
    authority.allowed=True;stamp[0]=.7
    with pytest.raises(Refused):await control.tick()
    assert renew==[True]
    await service.authorize(wire(grant,id=sid))
    await control.tick()
    assert renew==[True,True] and not control.negotiating


@pytest.mark.asyncio
async def test_idle_reservation_does_not_compete_with_native_authentication():
    class AuthorityWithCalls(Authority):
        def __init__(self):
            self.calls = []

        async def check(self, scope):
            self.calls.append(scope)
            return await super().check(scope)

    class MediaWithoutActivePublisher(Media):
        async def paths(self):
            pytest.fail('An idle reservation cannot require a native path listing')

    store = Store()
    authority = AuthorityWithCalls()
    service = Admission(store, authority, GENERATION, clock=lambda: NOW)
    grant = await service.issue(SCOPE, 60)
    authority.allowed = False
    media = MediaWithoutActivePublisher()
    renewed = []
    control = Controller(service, media, lambda: renewed.append(True), clock=lambda: NOW)
    await control.tick()
    assert authority.calls == [SCOPE]
    assert store.rows[0]['phase'] == 0
    assert renewed == [True]

    request = wire(grant)
    with pytest.raises(Refused):
        await service.authorize(request)
    authority.allowed = True
    await service.authorize(request)
    assert store.rows[0]['phase'] == 1


@pytest.mark.asyncio
async def test_pending_expiry_does_not_close_concurrently_consumed_grant():
    class ConcurrentStore(Store):
        async def outstanding(self, generation):
            snapshot = await super().outstanding(generation)
            await service.authorize(request)
            return snapshot

    store = ConcurrentStore()
    service = Admission(store, Authority(), GENERATION, clock=lambda: NOW + 9)
    grant = await service.issue(SCOPE, 60)
    service.clock = lambda: NOW + 18
    request = wire(grant)
    await Controller(service, Media(), lambda: None, clock=lambda: NOW + 19).tick()
    assert store.rows[0]['phase'] == 1
    assert store.rows[0]['media_session'] == request['id']


@pytest.mark.asyncio
async def test_viewer_revocation_keeps_publisher_while_another_grant_is_pending():
    viewer = Scope(Subject(TENANT, 'viewer'), SCOPE.room, 'read', SCOPE.ip)
    pending = Scope(Subject(TENANT, 'pending'), 'other-room', 'publish', SCOPE.ip)

    class ScopedAuthority(Authority):
        revoked = False

        async def check(self, scope):
            if self.revoked and scope == pending:
                pytest.fail('An idle grant must not contend with revocation checks')
            return Decision(scope, not (self.revoked and scope == viewer), 2000, 'a' * 64)

    store = Store()
    authority = ScopedAuthority()
    service = Admission(store, authority, GENERATION, clock=lambda: NOW)
    publisher_grant = await service.issue(SCOPE, 60)
    publisher = wire(publisher_grant)
    await service.authorize(publisher)
    viewer_grant = await service.issue(viewer, 60)
    store.rows[-1]['path'] = viewer_grant['path'] = publisher_grant['path']
    reader = wire(viewer_grant, action='read')
    await service.authorize(reader)
    await service.issue(pending, 60)

    media = Media()
    media.values[publisher['id']] = {'path': publisher['path'], 'state': 'publish', 'inboundBytes': 1}
    media.values[reader['id']] = {'path': reader['path'], 'state': 'read', 'outboundBytes': 1}
    control = Controller(service, media, lambda: None, clock=lambda: NOW)
    await control.tick()
    authority.revoked = True
    await control.tick()

    assert media.kicked == [reader['id']]
    assert media.values.keys() == {publisher['id']}
    assert [row['phase'] for row in store.rows] == [1, 2, 0]


@pytest.mark.asyncio
async def test_replay_negotiation_leaves_original_publisher_running_until_callback_refusal():
    store = Store()
    authority = Authority()
    service = Admission(store, authority, GENERATION, clock=lambda: NOW)
    grant = await service.issue(SCOPE, 60)
    original = wire(grant)
    await service.authorize(original)
    replay = wire(grant)

    media = Media()
    media.values[original['id']] = {'path': grant['path'], 'state': 'publish', 'inboundBytes': 1}
    media.values[replay['id']] = {
        'path': grant['path'], 'state': 'publish', 'peerConnectionEstablished': False,
        'inboundBytes': 0, 'outboundBytes': 0,
    }
    renewed = []
    control = Controller(service, media, lambda: renewed.append(True), clock=lambda: NOW)
    await control.tick()

    with pytest.raises(Refused):
        await service.authorize(replay)
    assert len(store.rows) == 1
    assert store.rows[0]['media_session'] == original['id']
    assert renewed == [True]
    assert media.kicked == []

    media.values.pop(replay['id'])
    await control.tick()
    assert set(media.values) == {original['id']}
    assert not control.negotiating
    assert renewed == [True, True]


@pytest.mark.asyncio
async def test_path_poll_timeout_keeps_only_a_recent_authorized_publisher_observation():
    class BusyPaths(Media):
        busy = False

        async def paths(self):
            if self.busy:
                raise TimeoutError
            return await super().paths()

    stamp = [0.0]
    store = Store()
    authority = Authority()
    service = Admission(store, authority, GENERATION, clock=lambda: NOW)
    publisher = wire(await service.issue(SCOPE, 60))
    await service.authorize(publisher)
    viewer_scope = Scope(Subject(TENANT, 'viewer'), SCOPE.room, 'read', SCOPE.ip)
    viewer_grant = await service.issue(viewer_scope, 60)
    # The in-memory store does not implement the production reader reservation lookup.
    store.rows[-1]['path'] = publisher['path']
    viewer = wire(viewer_grant, action='read', path=publisher['path'])
    await service.authorize(viewer)
    media = BusyPaths()
    media.values[publisher['id']] = {'path': publisher['path'], 'state': 'publish', 'inboundBytes': 1}
    media.values[viewer['id']] = {'path': publisher['path'], 'state': 'read', 'outboundBytes': 1}
    renewed = []
    controller = Controller(service, media, lambda: renewed.append(True), clock=lambda: NOW,
                            monotonic=lambda: stamp[0])
    await controller.tick()

    media.busy = True
    stamp[0] = 0.25
    await controller.tick()
    assert media.kicked == []
    assert renewed == [True, True]

    stamp[0] = 0.5
    await controller.tick()
    assert media.kicked == [viewer['id']]
    assert controller.compatible_publishers == {}

    authority.allowed = False
    stamp[0] = 0.6
    await controller.tick()
    assert media.kicked == [viewer['id'], publisher['id']]
