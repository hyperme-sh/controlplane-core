"""Private native API and inactive composition contracts, no listeners started."""
import asyncio
import httpx
import pytest
from api.streaming.mediamtx import MediaMTX, compatible_path
from api.streaming.contracts import Refused
from api.streaming.policy import DAILY_BYTES, PROVISIONING_ENABLED, STREAMING_ENABLED
from api.streaming.runtime import configuration, wait_for_first_tick


def test_inactive_policy_and_native_codec_profile():
    assert DAILY_BYTES==32_000_000_000 and PROVISIONING_ENABLED is False and STREAMING_ENABLED is False
    row={'online':True,'tracks2':[{'codec':'H264','codecProps':{'profile':'Baseline'}},{'codec':'Opus','codecProps':{'channelCount':2}}]}
    assert compatible_path(row)
    row['tracks2'][0]['codecProps']['profile']='Main'
    assert not compatible_path(row)
    for invalid in (None,{}, {'online':True,'tracks2':[]}, {'online':True,'tracks2':[None,None]}):
        assert not compatible_path(invalid)


def test_disabled_private_config_does_not_require_or_create_resources(tmp_path):
    path=tmp_path/'disabled.json';path.write_text('{"version":1,"enabled":false}');path.chmod(0o600)
    with pytest.raises(Refused):configuration(str(path))
    assert list(tmp_path.iterdir())==[path]


@pytest.mark.asyncio
async def test_native_api_is_loopback_only_bounded_and_rejects_redirects():
    mode='empty'
    def respond(request):
        if mode=='redirect':return httpx.Response(302,headers={'Location':'https://external.example'})
        if mode=='large':return httpx.Response(200,content=b'x'*65537)
        if mode=='unknown':return httpx.Response(200,json={'items':[],'itemCount':1,'pageCount':1})
        return httpx.Response(200,json={'items':[],'itemCount':0,'pageCount':0})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        for origin in ('http://external.example:9997','http://127.0.0.1:9997/path','http://user:pass@127.0.0.1:9997'):
            with pytest.raises(Refused):MediaMTX(client,origin)
        media=MediaMTX(client,'http://127.0.0.1:9997')
        assert await media.sessions()=={} and await media.paths()=={}
        for mode in ('redirect','large','unknown'):
            with pytest.raises(Refused):await media.sessions()


@pytest.mark.asyncio
async def test_native_path_listing_can_wait_for_auth_but_never_hang():
    blocked=False
    async def respond(request):
        if blocked:await asyncio.Event().wait()
        await asyncio.sleep(.3)
        return httpx.Response(200,json={'items':[],'itemCount':0,'pageCount':0})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        media=MediaMTX(client,'http://127.0.0.1:9997')
        assert await media.paths()=={}
        blocked=True
        async with asyncio.timeout(1.5):
            with pytest.raises(TimeoutError):await media.paths()


@pytest.mark.asyncio
async def test_kick_accepts_only_typed_native_absence_after_disconnect_race():
    body={'status':'error','error':'session not found'};status=404
    def respond(request):return httpx.Response(status,json=body)
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        media=MediaMTX(client,'http://127.0.0.1:9997')
        await media.kick('11111111-1111-4111-8111-111111111111')
        with pytest.raises(Refused):await media.sessions()
        for body in ({'error':'route not found'},{}, {'error':'session not found','other':True}):
            with pytest.raises(Refused):await media.kick('11111111-1111-4111-8111-111111111111')
        body={'status':'error','error':'session not found'};status=500
        with pytest.raises(Refused):await media.kick('11111111-1111-4111-8111-111111111111')


@pytest.mark.asyncio
async def test_first_tick_waits_for_private_listener_and_refuses_a_stopped_guard():
    attempts = 0
    completed = []

    async def tick():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("Private listener is still starting")
        completed.append(True)

    await wait_for_first_tick(tick, lambda: True)
    assert attempts == 2
    assert completed == [True]

    with pytest.raises(Refused, match="Stream process stopped"):
        await wait_for_first_tick(tick, lambda: False)
    assert attempts == 2


@pytest.mark.asyncio
async def test_first_tick_does_not_retry_an_invalid_management_response():
    attempts = 0

    async def tick():
        nonlocal attempts
        attempts += 1
        raise Refused("Invalid management response")

    with pytest.raises(Refused, match="Invalid management response"):
        await wait_for_first_tick(tick, lambda: True)
    assert attempts == 1
