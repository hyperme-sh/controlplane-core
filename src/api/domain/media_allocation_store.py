"""Private conditional allocation registry; no issuer endpoint or key activation."""
from dataclasses import asdict, dataclass
import json
import secrets
from collections.abc import Callable
from uuid import UUID

from api.domain.media_allocation_grant import (
    AllocationRejected, AllocationScope, MAX_GRANT_SECONDS, _integer, _unique, _uuid,
)
from api.domain.tenant_records import MAGIC, RecordBackend, RecordConflict, decode, provider_etag

MAX_ALLOCATIONS = 64
MAX_LEDGER_BYTES = 131072


@dataclass(frozen=True)
class Reservation:
    scope: AllocationScope
    allocation: str
    issued_at: int
    expires_at: int

    def validate(self) -> None:
        self.scope.claims()
        _uuid(self.allocation)
        _integer(self.issued_at)
        _integer(self.expires_at)
        if not 0 < self.expires_at - self.issued_at <= MAX_GRANT_SECONDS:
            raise AllocationRejected("Invalid reservation interval")


class AllocationStore:
    """One immutable allocation per tenant/object, across all source bindings.

    The caller must establish current owner, policy and requester authority before
    calling reserve, and recheck it in before_write. Provider CAS serializes writes;
    it cannot detect restoring an entire provider snapshot. Independent source
    checkpoints and operator recovery controls remain required before activation.
    """
    def __init__(self, tenant: str, issuer: str, backend: RecordBackend):
        self.tenant, self.issuer = _uuid(tenant), _uuid(issuer)
        self.backend = backend
        # Neither public named records nor ciphertext objects can address this key.
        # Do not partition by source: that would permit two sources for one object.
        self.key = f"tenant-media-allocations/v1/{UUID(tenant).hex}/registry"

    def _validate(self, entries: tuple[Reservation, ...]) -> None:
        if len(entries) > MAX_ALLOCATIONS:
            raise AllocationRejected("Allocation quota exceeded")
        objects, allocations = set(), set()
        for entry in entries:
            entry.validate()
            if entry.scope.tenant != self.tenant or entry.scope.issuer != self.issuer:
                raise AllocationRejected("Allocation registry scope mismatch")
            if entry.scope.object in objects or entry.allocation in allocations:
                raise AllocationRejected("Duplicate allocation registry entry")
            objects.add(entry.scope.object)
            allocations.add(entry.allocation)

    def _encode(self, entries: tuple[Reservation, ...]) -> bytes:
        self._validate(entries)
        value = {"version": 1, "tenant": self.tenant, "issuer": self.issuer,
                 "entries": [asdict(entry) for entry in entries]}
        return MAGIC + secrets.token_bytes(32) + json.dumps(value, separators=(",", ":"), sort_keys=True).encode()

    def _parse(self, raw: bytes) -> tuple[Reservation, ...]:
        if type(raw) is not bytes or len(raw) > MAX_LEDGER_BYTES:
            raise AllocationRejected("Invalid allocation registry size")
        try:
            value = json.loads(decode(raw).data, object_pairs_hook=_unique)
            if (type(value) is not dict or set(value) != {"version", "tenant", "issuer", "entries"}
                    or type(value["version"]) is not int or value["version"] != 1
                    or value["tenant"] != self.tenant or value["issuer"] != self.issuer
                    or type(value["entries"]) is not list):
                raise AllocationRejected("Invalid allocation registry")
            entries = tuple(Reservation(scope=AllocationScope(**row["scope"]),
                allocation=row["allocation"], issued_at=row["issued_at"], expires_at=row["expires_at"])
                for row in value["entries"]
                if type(row) is dict and set(row) == {"scope", "allocation", "issued_at", "expires_at"})
            if len(entries) != len(value["entries"]):
                raise AllocationRejected("Invalid allocation registry entry")
            self._validate(entries)
            return entries
        except (TypeError, KeyError, ValueError, RecursionError) as exc:
            raise AllocationRejected("Invalid allocation registry") from exc

    async def initialize(self) -> None:
        """Explicit operator bootstrap only; never call on a missing runtime read.

        The provider must refuse overwriting any existing object. Loss of state
        requires controlled recovery, not calling this method again.
        """
        await self.backend.replace(self.key, self._encode(()), None)

    async def _load(self) -> tuple[tuple[Reservation, ...], str]:
        found = await self.backend.fetch(self.key)
        if found is None:
            raise AllocationRejected("Allocation registry unavailable")
        return self._parse(found[0]), provider_etag(found[1])

    async def reserve(self, candidate: Reservation, *, now: int,
                      before_write: Callable[[], None]) -> Reservation:
        """Return the original same-scope reservation; never renew or replace it.

        A CAS conflict propagates. The caller may reread using the same request,
        after checking current authority again. Expired reservations still occupy
        their object and quota. No delete, cancellation reclamation or reset API.
        """
        _integer(now)
        self._validate((candidate,))
        if not candidate.issued_at <= now < candidate.expires_at:
            raise AllocationRejected("Reservation not current")
        entries, etag = await self._load()
        for entry in entries:
            if entry.scope.object == candidate.scope.object:
                if entry.scope != candidate.scope:
                    raise RecordConflict("Object already allocated to another scope")
                before_write()
                return entry
        if any(entry.allocation == candidate.allocation for entry in entries):
            raise RecordConflict("Allocation identifier already used")
        encoded = self._encode((*entries, candidate))
        before_write()
        await self.backend.replace(self.key, encoded, etag)
        return candidate
