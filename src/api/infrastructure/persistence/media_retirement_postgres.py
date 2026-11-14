"""Authenticated start of permanent media retirement; no provider deletion."""
import re
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from psycopg import sql

from api.domain.media_allocation_grant import AllocationRejected, _integer, _uuid
from api.domain.media_retirement_request import RetirementScope, verify_retirement_request
from api.domain.tenant_records import RecordConflict


class PostgresMediaRetirement:
    def __init__(self, store, *, source, request_keys, clock):
        self.store,self.source,self.clock=store,_uuid(source),clock
        if (not 1<=len(request_keys)<=16
                or any(type(k) is not str or not re.fullmatch('[A-Za-z0-9_-]{1,64}',k) for k in request_keys)
                or any(not isinstance(v,Ed25519PublicKey) for v in request_keys.values())):
            raise AllocationRejected('Invalid retirement source keys')
        self.keys=dict(request_keys)

    async def begin(self, raw, *, object,owner,command,cancel):
        scope=RetirementScope(self.store.issuer,self.store.tenant,self.source,object,owner,command)
        def guard():
            cancel()
            now=self.clock();_integer(now)
            return verify_retirement_request(raw,expected=scope,keys=self.keys,now=now)
        guard()
        async with self.store._locked() as cursor:
            await cursor.execute(sql.SQL('SELECT issuer_id,source_id,owner_principal,phase,deletion_command FROM {} WHERE tenant_id=%s AND object_id=%s FOR UPDATE').format(self.store._table()),(self.store.tenant,object))
            row=await cursor.fetchone()
            if row is None: raise AllocationRejected('Retirement allocation unavailable')
            if str(row['issuer_id'])!=scope.issuer or str(row['source_id'])!=scope.source or row['owner_principal']!=scope.owner:
                raise RecordConflict('Retirement allocation scope mismatch')
            if row['phase']!=0:
                if row['deletion_command']!=command: raise RecordConflict('Different retirement command')
                guard()
                return row['phase']
            guard()
            await cursor.execute(sql.SQL('UPDATE {} SET phase=1,deletion_command=%s,updated_at=now() WHERE tenant_id=%s AND object_id=%s').format(self.store._table()),(command,self.store.tenant,object))
            guard()
        return 1
