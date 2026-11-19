"""Read-only registry and provider metadata for a separately scoped observer."""
import hashlib
import re
from uuid import UUID
from psycopg import sql
from api.domain.media_allocation_grant import _uuid
from api.domain.media_allocation_store import MAX_ALLOCATIONS
from api.domain.tenant_objects import ObjectInputError, ObjectMetadata, ObjectUnavailable
from api.domain.tenant_records import RecordConflict
from api.infrastructure.externals.media_retirement_backend import RETIRED, media_key


def observation_request(object, references):
    if type(object) is not str or not re.fullmatch(r'media_p_[0-9a-f]{128}_[0-9a-f]{32}', object):
        raise ObjectInputError('Invalid observation object')
    if type(references) is not list or len(references) > 8:
        raise ObjectInputError('Invalid observation references')
    seen = set()
    for ref in references:
        if (type(ref) is not dict or set(ref) != {'kind', 'digest'} or
                ref['kind'] not in ('ciphertext', 'wrap') or type(ref['digest']) is not str or
                not re.fullmatch('[0-9a-f]{64}', ref['digest'])):
            raise ObjectInputError('Invalid observation reference')
        key = (ref['kind'], ref['digest'])
        if key in seen:
            raise ObjectInputError('Duplicate observation reference')
        seen.add(key)


async def _registry_snapshot(media, object, requires_location, guard, *, include_allocation):
    store = media.store
    guard()
    async with store._locked() as cursor:
        row = await media._object(cursor, object)
        owner, phase, command = row['owner_principal'], row['phase'], row['deletion_command']
        if (type(owner) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{43}\.[A-Za-z0-9_-]{43}', owner) or
                type(phase) is not int or phase not in (0, 1, 2) or
                (phase == 0 and command is not None) or
                (phase != 0 and (type(command) is not str or not re.fullmatch('[0-9a-f]{64}', command)))):
            raise ObjectUnavailable('Invalid media observation state')
        await cursor.execute(sql.SQL('SELECT provider_location FROM {} WHERE tenant_id=%s AND object_id=%s').format(media.locations), (store.tenant, object))
        location = await cursor.fetchone()
        if location is not None and location['provider_location'] != media.location:
            raise RecordConflict('Media provider location changed')
        await cursor.execute(sql.SQL('SELECT kind,digest,byte_length FROM {} WHERE tenant_id=%s AND object_id=%s ORDER BY kind,digest LIMIT 130').format(media.refs), (store.tenant, object))
        rows = await cursor.fetchall()
        if len(rows) > 129:
            raise ObjectUnavailable('Media reference bound exceeded')
        held, seen = [], set()
        for ref in rows:
            kind, digest, length = ref['kind'], ref['digest'], ref['byte_length']
            observation_request(object, [{'kind': kind, 'digest': digest}])
            if (type(length) is not int or not (33 if kind == 'ciphertext' else 1) <= length <= (64*1024*1024 if kind == 'ciphertext' else 65536)
                    or (kind, digest) in seen):
                raise ObjectUnavailable('Invalid media reference')
            seen.add((kind, digest))
            held.append({'kind': kind, 'digest': digest, 'length': length})
        if sum(ref['kind'] == 'ciphertext' for ref in held) > 1 or (phase == 2 and held):
            raise ObjectUnavailable('Invalid media reference state')
        if location is None and (held or requires_location):
            raise RecordConflict('Media provider location requires reconciliation')
        await cursor.execute(sql.SQL("SELECT COUNT(*) FILTER (WHERE o.owner_principal=%s) AS owner_objects, COALESCE(SUM(r.byte_length) FILTER (WHERE o.owner_principal=%s),0)::bigint AS owner_bytes, COALESCE(SUM(r.byte_length),0)::bigint AS tenant_bytes FROM {} r JOIN {} o USING(tenant_id,object_id) WHERE r.tenant_id=%s AND r.kind='ciphertext'").format(media.refs, store._table()), (owner, owner, store.tenant))
        totals = await cursor.fetchone()
        quota = {}
        for name, column in [('ownerObjects', 'owner_objects'), ('ownerBytes', 'owner_bytes'), ('tenantBytes', 'tenant_bytes')]:
            value = totals[column]
            if type(value) is not int or not 0 <= value <= 2**53-1:
                raise ObjectUnavailable('Invalid media quota observation')
            quota[name] = value
        result = {'tenant': store.tenant, 'issuer': store.issuer, 'object': object, 'owner': owner,
                  'phase': phase, 'command': command, 'providerLocation': media.location if location else None,
                  'references': held, 'quota': quota, 'observed': []}
        if include_allocation:
            identifier = _uuid(str(row['allocation_id']))
            await cursor.execute(sql.SQL(
                'SELECT count(*) AS active FROM {} WHERE tenant_id=%s AND issuer_id=%s AND owner_principal=%s AND phase<>2'
            ).format(store._table()), (store.tenant, store.issuer, owner))
            active = (await cursor.fetchone())['active']
            if type(active) is not int or not int(phase != 2) <= active <= MAX_ALLOCATIONS:
                raise ObjectUnavailable('Invalid media allocation count')
            scope = (store.tenant, store.issuer, object, identifier)
            result['allocation'] = {
                'digest': hashlib.sha256(('storage-media-allocation-v1\0' + '\0'.join(scope)).encode()).hexdigest(),
                'ownerActive': active,
            }
        guard()
        return result


async def observe_media(media, *, object, references, guard, include_allocation=False):
    """Observe tenant metadata without holding its lock during provider I/O.

    Requested digests need the caller's retained pre-cleanup membership snapshot.
    Registry snapshots must match before and after external observations; this
    is not an atomic provider/registry transaction or an authorization to mutate.
    """
    observation_request(object, references)
    first = await _registry_snapshot(media, object, bool(references), guard, include_allocation=include_allocation)
    store = media.store
    observed = []
    for ref in references:
        guard()
        metadata = await media.backend.head(media_key(UUID(store.tenant), ref['kind'], ref['digest']))
        if metadata is None:
            state, length = 'missing', None
        elif metadata == RETIRED:
            state, length = 'retired', 0
        elif (isinstance(metadata, ObjectMetadata) and metadata.sha256 == ref['digest'] and
              type(metadata.size) is int and (33 if ref['kind'] == 'ciphertext' else 1) <= metadata.size <= (64*1024*1024 if ref['kind'] == 'ciphertext' else 65536)):
            state, length = 'present', metadata.size
        else:
            raise ObjectUnavailable('Invalid provider observation')
        observed.append({**ref, 'state': state, 'length': length})
    guard()
    if references:
        second = await _registry_snapshot(media, object, True, guard, include_allocation=include_allocation)
        if first != second:
            raise RecordConflict('Media observation changed')
    return {**first, 'observed': observed}
