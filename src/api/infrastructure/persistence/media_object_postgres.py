"""Durable media references and serialized provider retirement; no HTTP activation."""
import hashlib
import re
from tempfile import SpooledTemporaryFile
from uuid import UUID
from psycopg import sql

from api.domain.media_allocation_grant import AllocationRejected
from api.domain.tenant_objects import ObjectInputError,ObjectMetadata,ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.infrastructure.externals.media_retirement_backend import media_key

MAX_OWNER_OBJECTS=8
MAX_OWNER_BYTES=64*1024*1024
MAX_TENANT_BYTES=256*1024*1024
MAX_WRAPS=128


class PostgresMediaObjects:
    """Requires an authenticated caller and an explicitly prepared allocation store.

    A reference commits before any provider write, so failures retain quota and
    leave a cleanup target. A second tenant lock fences writes against retirement.
    Permanent provider markers prevent delayed create-only writes from restoring
    payloads even after a process interruption. Only phase 1 plus its stored
    command permits cleanup; no supplied list of digests is trusted.
    """
    def __init__(self,store,backend):
        self.store,self.backend=store,backend
        self.refs=sql.Identifier(store.schema,'storage_media_refs')

    async def _object(self,cursor,object,owner=None):
        if type(object) is not str or len(object)>200:
            raise ObjectInputError('Invalid media object')
        await cursor.execute(sql.SQL('SELECT * FROM {} WHERE tenant_id=%s AND object_id=%s FOR UPDATE').format(self.store._table()),(self.store.tenant,object))
        row=await cursor.fetchone()
        if row is None or str(row['issuer_id'])!=self.store.issuer or (owner is not None and row['owner_principal']!=owner):
            raise AllocationRejected('Media allocation unavailable')
        return row

    async def _reserve(self,object,owner,kind,digest,size,guard):
        async with self.store._locked() as cursor:
            row=await self._object(cursor,object,owner)
            if row['phase']!=0:raise RecordConflict('Media object retired')
            await cursor.execute(sql.SQL('SELECT kind,digest,byte_length FROM {} WHERE tenant_id=%s AND object_id=%s').format(self.refs),(self.store.tenant,object))
            held=await cursor.fetchall()
            for ref in held:
                if ref['kind']==kind and ref['digest']==digest:
                    if ref['byte_length']!=size:raise RecordConflict('Media reference length differs')
                    guard();return
            # Cleanup uses this same tenant lock. Refuse an existing permanent
            # marker before charging a fresh reference, so callers can re-encrypt.
            found=await self.backend.head(media_key(UUID(self.store.tenant),kind,digest))
            if found is not None and found!=ObjectMetadata(size,digest):
                raise ObjectUnavailable('Media payload unavailable; use fresh encrypted bytes')
            if kind=='ciphertext':
                if any(ref['kind']=='ciphertext' for ref in held):raise RecordConflict('Media ciphertext is immutable')
                await cursor.execute(sql.SQL("SELECT o.owner_principal,r.byte_length FROM {} r JOIN {} o USING(tenant_id,object_id) WHERE r.tenant_id=%s AND r.kind='ciphertext'").format(self.refs,self.store._table()),(self.store.tenant,))
                charged=await cursor.fetchall()
                own=[ref for ref in charged if ref['owner_principal']==owner]
                if len(own)>=MAX_OWNER_OBJECTS or sum(ref['byte_length'] for ref in own)+size>MAX_OWNER_BYTES or sum(ref['byte_length'] for ref in charged)+size>MAX_TENANT_BYTES:
                    raise AllocationRejected('Media quota exceeded')
            elif sum(ref['kind']=='wrap' for ref in held)>=MAX_WRAPS:
                raise AllocationRejected('Media wrap quota exceeded')
            guard()
            await cursor.execute(sql.SQL('INSERT INTO {} (tenant_id,object_id,kind,digest,byte_length) VALUES (%s,%s,%s,%s,%s)').format(self.refs),(self.store.tenant,object,kind,digest,size))
            guard()

    async def put(self,*,object,owner,kind,digest,size,chunks,guard):
        if type(owner) is not str or len(owner)!=87:raise ObjectInputError('Invalid media owner')
        key=media_key(UUID(self.store.tenant),kind,digest)
        minimum,maximum=(33,64*1024*1024) if kind=='ciphertext' else (1,65536)
        if type(size) is not int or not minimum<=size<=maximum:raise ObjectInputError('Invalid media size')
        guard()
        with SpooledTemporaryFile(max_size=1024*1024,mode='w+b') as body:
            hashed,seen=hashlib.sha256(),0
            async for chunk in chunks:
                if type(chunk) is not bytes:raise ObjectInputError('Invalid media body')
                seen+=len(chunk)
                if seen>size:raise ObjectInputError('Media body exceeds declared size')
                hashed.update(chunk);body.write(chunk);guard()
            if seen!=size or hashed.hexdigest()!=digest:raise ObjectInputError('Media checksum or size mismatch')
            await self._reserve(object,owner,kind,digest,size,guard)
            # The durable reference survives errors in this second transaction.
            async with self.store._locked() as cursor:
                row=await self._object(cursor,object,owner)
                if row['phase']!=0:raise RecordConflict('Media object retired')
                guard();body.seek(0)
                metadata=ObjectMetadata(size,digest)
                created=await self.backend.put(key,body,metadata)
                if not created and await self.backend.head(key)!=metadata:
                    raise ObjectUnavailable('Media provider object unavailable')
                guard()
            return created

    async def cleanup(self,*,object,command,guard):
        if type(command) is not str or not re.fullmatch('[0-9a-f]{64}',command):
            raise ObjectInputError('Invalid retirement command')
        guard()
        async with self.store._locked() as cursor:
            row=await self._object(cursor,object)
            if row['phase'] not in (1,2) or row['deletion_command']!=command:
                raise RecordConflict('Retirement command not accepted')
            if row['phase']==2:
                guard();return False
            await cursor.execute(sql.SQL('SELECT kind,digest FROM {} WHERE tenant_id=%s AND object_id=%s ORDER BY kind,digest').format(self.refs),(self.store.tenant,object))
            refs=await cursor.fetchall()
            for ref in refs:
                await cursor.execute(sql.SQL('SELECT 1 FROM {} WHERE tenant_id=%s AND kind=%s AND digest=%s AND object_id<>%s LIMIT 1').format(self.refs),(self.store.tenant,ref['kind'],ref['digest'],object))
                shared=await cursor.fetchone()
                guard()
                if shared is None:
                    await self.backend.retire(media_key(UUID(self.store.tenant),ref['kind'],ref['digest']))
                guard()
                await cursor.execute(sql.SQL('DELETE FROM {} WHERE tenant_id=%s AND object_id=%s AND kind=%s AND digest=%s').format(self.refs),(self.store.tenant,object,ref['kind'],ref['digest']))
            guard()
            await cursor.execute(sql.SQL('UPDATE {} SET phase=2,updated_at=now() WHERE tenant_id=%s AND object_id=%s').format(self.store._table()),(self.store.tenant,object))
            guard()
        return True
