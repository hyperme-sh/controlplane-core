"""Fail-closed orchestration; no fallback identities, grants or in-memory ledger."""
import asyncio
from datetime import datetime, timezone
import hashlib
import secrets
import time
from uuid import uuid4
from .contracts import AUTHORITY_TIMEOUT, Decision, Refused, Scope, callback, uuid


class Admission:
    def __init__(self, store, authority, generation, *, clock=time.time):
        self.store, self.authority = store, authority
        self.generation, self.clock = uuid(generation), clock

    async def check(self, scope):
        # A full current Check is mandatory on issue, consume, and every poll.
        async with asyncio.timeout(AUTHORITY_TIMEOUT):
            decision = await self.authority.check(scope)
        if type(decision) is not Decision: raise Refused('Current room authority required')
        decision.validate(scope, int(self.clock()))
        return decision

    async def issue(self, scope: Scope, seconds: int):
        if type(seconds) is not int or not 1 <= seconds <= 3600:
            raise Refused('Invalid stream duration')
        decision = await self.check(scope)
        now = int(self.clock())
        deadline = min(now + seconds, decision.valid_until, (now // 86400 + 1) * 86400)
        if deadline <= now: raise Refused('Stream expired')
        token = secrets.token_hex(32)
        row = await self.store.reserve(scope, self.generation, str(uuid4()),
            hashlib.sha256(token.encode()).hexdigest(), now, deadline)
        return {'token': token, 'path': row['path'], 'expiresAt': row['expires_at'],
                'reservation': str(row['reservation_id'])}

    async def authorize(self, value):
        value = callback(value)
        digest = hashlib.sha256(value['token'].encode()).hexdigest()
        row = await self.store.lookup(value['path'], digest, self.generation)
        scope = self.store.scope(row)
        if (scope.action != value['action'] or scope.ip != value['ip']
                or row['path'] != value['path']): raise Refused('Stream scope mismatch')
        await self.check(scope)
        now = int(self.clock())
        # The transaction rechecks pending state, generation, deadline and path.
        # Successful consumption is never repeated, even for the same session ID.
        await self.store.consume(row, value['id'], now)
        return {'expireAt': datetime.fromtimestamp(row['expires_at'], timezone.utc).isoformat().replace('+00:00','Z')}
