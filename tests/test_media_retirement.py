"""Acceptance must precede cleanup; retries preserve the permanent command."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from api.domain.media_allocation_grant import AllocationRejected
from api.domain.media_retirement import MediaRetirement


@pytest.mark.asyncio
async def test_accept_then_cleanup_and_completed_retry():
    calls=[];store=object();command='a'*64
    async def begin(*args,**kwargs):calls.append(('begin',args,kwargs));return 1
    async def cleanup(**kwargs):calls.append(('cleanup',kwargs));return True
    authority=SimpleNamespace(store=store,begin=AsyncMock(side_effect=begin))
    objects=SimpleNamespace(store=store,cleanup=AsyncMock(side_effect=cleanup))
    service=MediaRetirement(authority,objects);cancel=lambda:None
    result=await service.retire(b'proof',object='media',owner='owner',command=command,cancel=cancel)
    assert result=={'accepted':True,'command':command,'cleanup':'complete'}
    assert [row[0] for row in calls]==['begin','cleanup']
    assert calls[0][1]==(b'proof',)
    assert calls[1][1]=={'object':'media','command':command,'guard':cancel}
    authority.begin.side_effect=None;authority.begin.return_value=2
    assert await service.retire(b'fresh proof',object='media',owner='owner',command=command,cancel=cancel)==result
    assert objects.cleanup.await_count==1


@pytest.mark.asyncio
async def test_refusal_uncertainty_and_cancel_never_report_completion():
    store=object();authority=SimpleNamespace(store=store,begin=AsyncMock(return_value=1))
    objects=SimpleNamespace(store=store,cleanup=AsyncMock(return_value=True));service=MediaRetirement(authority,objects)
    args=dict(object='media',owner='owner',command='a'*64,cancel=lambda:None)
    authority.begin.side_effect=AllocationRejected()
    with pytest.raises(AllocationRejected):await service.retire(b'proof',**args)
    objects.cleanup.assert_not_awaited();authority.begin.side_effect=None
    for phase in [0,3,True,None]:
        authority.begin.return_value=phase
        with pytest.raises(AllocationRejected):await service.retire(b'proof',**args)
    objects.cleanup.assert_not_awaited();authority.begin.return_value=1
    objects.cleanup.side_effect=TimeoutError()
    with pytest.raises(TimeoutError):await service.retire(b'proof',**args)
    objects.cleanup.side_effect=None
    def cancel():raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):await service.retire(b'proof',**{**args,'cancel':cancel})
    assert objects.cleanup.await_count==1
    assert (await service.retire(b'retry',**args))['cleanup']=='complete'


def test_registry_mismatch_refused():
    with pytest.raises(ValueError):MediaRetirement(SimpleNamespace(store=object()),SimpleNamespace(store=object()))
