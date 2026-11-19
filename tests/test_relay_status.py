"""Read-only SQL adapter doubles; real owner database checks run in CI."""
from contextlib import contextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from api.infrastructure.messaging.relay_status import queue_status


class Engine:
    def __init__(self, row): self.row=row; self.calls=[]
    @contextmanager
    def begin(self): yield self
    def execute(self, sql, values):
        self.calls.append((str(sql),values))
        return SimpleNamespace(mappings=lambda:SimpleNamespace(one=lambda:self.row))


def test_queue_marks_only_read_declared_tenant_and_hide_payloads():
    tenant=uuid4(); engine=Engine({'pending':2,'oldest_age':12,'newest_age':3})
    assert queue_status(engine,tenant)=={'pending':2,'oldest_age_seconds':12.0}
    assert engine.calls[0][1]=={'tenant':str(tenant)}
    assert engine.calls[1][1]=={'tenant':tenant}
    assert 'delivered_at IS NULL' in engine.calls[1][0]
    assert 'envelope' not in engine.calls[1][0]


@pytest.mark.parametrize('row', [
    {'pending':-1,'oldest_age':0,'newest_age':0},
    {'pending':0,'oldest_age':1,'newest_age':1},
    {'pending':1,'oldest_age':None,'newest_age':None},
    {'pending':1,'oldest_age':3,'newest_age':-1},
    {'pending':1,'oldest_age':float('inf'),'newest_age':0},
])
def test_invalid_or_future_queue_marks_refuse(row):
    with pytest.raises(ValueError): queue_status(Engine(row),uuid4())


def test_empty_queue_is_explicit_not_an_invented_sample():
    assert queue_status(Engine({'pending':0,'oldest_age':None,'newest_age':None}),uuid4())=={'pending':0,'oldest_age_seconds':None}
