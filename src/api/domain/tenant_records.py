"""Named opaque records with conditional updates, separate from media objects."""
from dataclasses import dataclass
import hashlib
import re
import secrets
import time
from typing import Protocol
from uuid import UUID

from api.domain.storage_scope import storage_tenant
from api.domain.tenant_objects import ObjectInputError, ObjectUnavailable

MAX_RECORD_BYTES = 8 * 1024 * 1024
MAGIC = b"SPANREC1"
HEADER_BYTES = len(MAGIC) + 32
MAX_STORED_BYTES = MAX_RECORD_BYTES + HEADER_BYTES


class RecordConflict(RuntimeError):
    pass


class RecordBackend(Protocol):
    async def fetch(self, key: str) -> tuple[bytes, str] | None: ...
    async def replace(self, key: str, data: bytes, etag: str | None) -> None: ...
    async def remove(self, key: str, etag: str) -> None: ...


@dataclass(frozen=True)
class Record:
    data: bytes
    revision: str


def provider_etag(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'"[A-Za-z0-9._:-]{1,128}"', value):
        raise ObjectUnavailable("Invalid provider revision")
    return value


def record_key(tenant: UUID, namespace: str, name: str) -> str:
    if namespace not in ("cache", "recovery", "fixtures", "media", "experiments") or not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", name):
        raise ObjectInputError("Invalid record name")
    from api.domain.experiments.records import physical_record
    namespace, name = physical_record(namespace, name)
    return f"tenant-records/v1/{storage_tenant(tenant).hex}/{namespace}/{name}"


def decode(data: bytes) -> Record:
    if not isinstance(data, bytes) or not HEADER_BYTES < len(data) <= MAX_STORED_BYTES or not data.startswith(MAGIC):
        raise ObjectUnavailable("Invalid stored record")
    return Record(data[HEADER_BYTES:], hashlib.sha256(data).hexdigest())


class TenantRecords:
    def __init__(self, tenant: UUID, backend: RecordBackend):
        self.tenant, self.backend = storage_tenant(tenant), backend

    async def get(self, namespace: str, name: str) -> Record | None:
        stored = await self.backend.fetch(record_key(self.tenant, namespace, name))
        if stored is None:
            return None
        provider_etag(stored[1])
        return decode(stored[0])

    async def put(self, namespace: str, name: str, data: bytes, expected: str | None) -> Record:
        key = record_key(self.tenant, namespace, name)
        if not isinstance(data, bytes) or not 1 <= len(data) <= MAX_RECORD_BYTES:
            raise ObjectInputError("Invalid record size")
        if expected is not None and (not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected)):
            raise ObjectInputError("Invalid record revision")
        current = await self.backend.fetch(key)
        if current is None:
            if expected is not None:
                raise RecordConflict("Record changed")
            etag = None
        else:
            if expected is None or decode(current[0]).revision != expected:
                raise RecordConflict("Record changed")
            etag = provider_etag(current[1])
        if namespace == "experiments":
            from api.domain.experiments.records import validate_write
            validate_write(name, data, decode(current[0]).data if current else None, int(time.time() * 1000))
        # A new nonce changes the stored bytes even when payloads repeat. S3's
        # content-derived ETag must not admit stale writes after an ABA cycle.
        encoded = MAGIC + secrets.token_bytes(32) + data
        await self.backend.replace(key, encoded, etag)
        return decode(encoded)

    async def delete(self, namespace: str, name: str, expected: str) -> None:
        if namespace == "experiments":
            raise ObjectInputError("Experiment records require revocation until expiry")
        key = record_key(self.tenant, namespace, name)
        if not isinstance(expected, str) or not re.fullmatch(r"[a-f0-9]{64}", expected):
            raise ObjectInputError("Invalid record revision")
        current = await self.backend.fetch(key)
        if current is None or decode(current[0]).revision != expected:
            raise RecordConflict("Record changed")
        await self.backend.remove(key, provider_etag(current[1]))
