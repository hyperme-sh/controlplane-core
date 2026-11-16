"""Consume the DB owner's aggregate wire ledger; never bootstrap or refund it."""
from dataclasses import dataclass
from uuid import uuid4
from psycopg import sql
from .contracts import Refused, uuid
from .postgres import PostgresStreams

CAP = 32_000_000_000


@dataclass(frozen=True)
class WireGrant:
    reservation: str
    consumer: str
    generation: str
    purpose: str
    reserved_bytes: int
    expires_at: int


def amount(value):
    if type(value) is not int or not 1 <= value <= CAP:
        raise Refused('Invalid wire allowance')
    return value


class WireBudget(PostgresStreams):
    """A trusted per-namespace controller, not a browser-accessible API.

    Each reserve returns a newly claimed grant exactly once. An ambiguous commit
    must be abandoned, never retried with the same id or refunded. Every region
    connects to the same authoritative tenant ledger.
    """
    def __init__(self, tenant, connect, generation, consumer, purpose, *, schema='storage_owner'):
        super().__init__(tenant, connect, schema=schema)
        self.generation, self.consumer = uuid(generation), uuid(consumer)
        if purpose not in ('turn', 'mediamtx'):
            raise Refused('Unsupported company wire purpose')
        self.purpose = purpose

    async def reserve(self, reserved_bytes):
        reserved_bytes = amount(reserved_bytes)
        reservation = str(uuid4())
        async with self.locked() as cursor:
            # The database owns the clock and the UTC accounting boundary.
            await cursor.execute('SELECT floor(extract(epoch FROM clock_timestamp()))::bigint AS now')
            now = (await cursor.fetchone())['now']
            expires = min(now + 60, (now // 86400 + 1) * 86400)
            if expires - now < 3:
                raise Refused('Wire accounting boundary')
            await cursor.execute(sql.SQL('INSERT INTO {} '
                '(tenant_id,reservation_id,generation,consumer,purpose,issued_at,expires_at,reserved_bytes) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s,%s)').format(self.table('wire_reservations')),
                (self.tenant,reservation,self.generation,self.consumer,self.purpose,now,expires,reserved_bytes))
            await cursor.execute(sql.SQL('UPDATE {} SET claimed=true WHERE tenant_id=%s AND reservation_id=%s '
                'AND NOT claimed RETURNING reservation_id').format(self.table('wire_reservations')),
                (self.tenant,reservation))
            if await cursor.fetchone() is None:
                raise Refused('Wire claim unavailable')
        # Return only after commit; neither SQL failures nor connection loss can
        # expose a usable grant whose charge was rolled back.
        return WireGrant(reservation,self.consumer,self.generation,self.purpose,reserved_bytes,expires)

    async def remaining(self, grant):
        if (type(grant) is not WireGrant or grant.consumer != self.consumer
                or grant.generation != self.generation or grant.purpose != self.purpose):
            raise Refused('Wire consumer mismatch')
        async with self.locked() as cursor:
            await cursor.execute(sql.SQL('SELECT r.reserved_bytes,r.expires_at,p.daily_bytes,'
                'floor(extract(epoch FROM clock_timestamp()))::bigint AS now '
                'FROM {} r JOIN {} p USING (tenant_id) '
                'WHERE r.tenant_id=%s AND r.reservation_id=%s AND r.consumer=%s '
                'AND r.generation=%s AND r.purpose=%s AND r.claimed AND p.enabled AND p.generation=r.generation'
                ).format(self.table('wire_reservations'),self.table('wire_policies')),
                (self.tenant,grant.reservation,self.consumer,self.generation,self.purpose))
            row = await cursor.fetchone()
            if (row is None or row['reserved_bytes'] != grant.reserved_bytes
                    or row['expires_at'] != grant.expires_at or not 1 <= row['daily_bytes'] <= CAP):
                raise Refused('Wire policy unavailable')
            # One second for the fractional DB clock, one for installing a gate.
            # Caller must bound the whole query/install round trip independently.
            remaining = row['expires_at'] - row['now'] - 2
            if remaining <= 0:
                raise Refused('Wire grant expired')
            return remaining
