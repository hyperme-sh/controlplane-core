"""Offline aggregate wire consumer contracts; PostgreSQL behavior is DB-owned CI."""
from contextlib import asynccontextmanager
from uuid import uuid4
import pytest
from api.streaming.contracts import Refused
from api.streaming.wire import CAP, WireBudget, WireGrant, amount


class Cursor:
    def __init__(self, rows): self.rows=list(rows); self.calls=[]
    async def execute(self, query, params=None): self.calls.append((query,params))
    async def fetchone(self): return self.rows.pop(0)


class Budget(WireBudget):
    def __init__(self, rows, fail_commit=False):
        super().__init__(str(uuid4()),None,str(uuid4()),str(uuid4()),'turn')
        self.cursor=Cursor(rows);self.fail_commit=fail_commit;self.committed=False
    @asynccontextmanager
    async def locked(self):
        yield self.cursor
        if self.fail_commit: raise ConnectionError('ambiguous commit')
        self.committed=True


@pytest.mark.asyncio
async def test_grant_is_claimed_and_committed_before_return():
    budget=Budget([{'now':86410},{'reservation_id':'claimed'}])
    grant=await budget.reserve(1000)
    assert budget.committed and grant.expires_at==86470 and grant.reserved_bytes==1000
    assert len(budget.cursor.calls)==3
    assert budget.cursor.calls[1][1][0]==budget.tenant
    assert budget.cursor.calls[1][1][-1]==1000
    assert budget.cursor.calls[2][1][1]==grant.reservation


@pytest.mark.asyncio
async def test_failed_commit_and_missing_claim_never_return_allowance():
    for budget in (Budget([{'now':86410},None]),Budget([{'now':86410},{'reservation_id':'x'}],True)):
        with pytest.raises((Refused,ConnectionError)): await budget.reserve(1000)


@pytest.mark.asyncio
async def test_utc_boundary_is_split_and_last_seconds_refused():
    budget=Budget([{'now':86390},{'reservation_id':'x'}])
    assert (await budget.reserve(1000)).expires_at==86400
    with pytest.raises(Refused):await Budget([{'now':86398}]).reserve(1000)


@pytest.mark.asyncio
async def test_current_grant_requires_matching_native_row():
    budget=Budget([{'reserved_bytes':1000,'expires_at':100,'daily_bytes':CAP,'now':97}])
    grant=WireGrant(str(uuid4()),budget.consumer,budget.generation,'turn',1000,100)
    assert await budget.remaining(grant)==1
    for row in (None,{'reserved_bytes':1000,'expires_at':100,'daily_bytes':CAP,'now':98},
                {'reserved_bytes':999,'expires_at':100,'daily_bytes':CAP,'now':90}):
        budget.cursor.rows=[row]
        with pytest.raises(Refused):await budget.remaining(grant)


def test_finite_cap_and_purpose():
    for value in (True,0,-1,CAP+1,1.5):
        with pytest.raises(Refused):amount(value)
    with pytest.raises(Refused):WireBudget(str(uuid4()),None,str(uuid4()),str(uuid4()),'seed')
