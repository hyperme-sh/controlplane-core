"""Private tenant object protocol. IDs name opaque bytes, never permissions."""
from __future__ import annotations

from contextlib import aclosing
from dataclasses import dataclass
import hashlib
import re
from tempfile import SpooledTemporaryFile
from typing import AsyncIterator, BinaryIO, Protocol
from uuid import UUID

from api.domain.storage_scope import storage_tenant

MAX_OBJECT_BYTES = 64 * 1024 * 1024
CHUNK_BYTES = 64 * 1024
_ID = re.compile(r"[0-9a-f]{64}\Z")


class ObjectInputError(ValueError):
    pass


class ObjectUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class ObjectMetadata:
    size: int
    sha256: str


class ObjectBackend(Protocol):
    async def put(self, key: str, body: BinaryIO, metadata: ObjectMetadata) -> bool: ...
    async def head(self, key: str) -> ObjectMetadata | None: ...
    def read(self, key: str, start: int, length: int) -> AsyncIterator[bytes]: ...
    async def delete(self, key: str) -> None: ...


def object_key(tenant: UUID, object_id: str) -> str:
    tenant = storage_tenant(tenant)
    if not isinstance(object_id, str) or not _ID.fullmatch(object_id):
        raise ObjectInputError("Invalid object ID")
    return f"tenant-objects/v1/{tenant.hex}/{object_id}"


def byte_range(value: str | None, size: int) -> tuple[int, int]:
    if value is None:
        return 0, size
    if not isinstance(value, str) or len(value) > 80:
        raise ObjectInputError("Invalid byte range")
    match = re.fullmatch(r"bytes=([0-9]*)-([0-9]*)", value)
    if not match or not any(match.groups()):
        raise ObjectInputError("Invalid byte range")
    first, last = match.groups()
    if not first:
        length = int(last)
        if length < 1:
            raise ObjectInputError("Invalid byte range")
        return max(0, size - length), min(length, size)
    start = int(first)
    end = min(int(last), size - 1) if last else size - 1
    if start >= size or end < start:
        raise ObjectInputError("Unsatisfiable byte range")
    return start, end - start + 1


class TenantObjects:
    """One explicit authenticated tenant and its selected private backend.

    Uploads are bounded and checksum-verified before the provider is called.
    Atomic conditional writes make retries idempotent. IDs are SHA-256 of the
    opaque payload, so deleting and re-uploading cannot replace an ID's bytes.
    Encrypt with fresh client-held keys/nonces before calling this protocol.
    """

    def __init__(self, tenant: UUID, backend: ObjectBackend):
        self.tenant = storage_tenant(tenant)
        self.backend = backend

    async def put(self, object_id: str, size: int, chunks: AsyncIterator[bytes]) -> bool:
        key = object_key(self.tenant, object_id)
        if type(size) is not int or not 1 <= size <= MAX_OBJECT_BYTES:
            raise ObjectInputError("Invalid object size")
        digest, seen = hashlib.sha256(), 0
        # Temporary files contain only caller ciphertext and close on all exits.
        with SpooledTemporaryFile(max_size=1024 * 1024, mode="w+b") as spool:
            async for chunk in chunks:
                if not isinstance(chunk, bytes):
                    raise ObjectInputError("Invalid upload body")
                seen += len(chunk)
                if seen > size:
                    raise ObjectInputError("Upload exceeds declared size")
                digest.update(chunk)
                spool.write(chunk)
            if seen != size or digest.hexdigest() != object_id:
                raise ObjectInputError("Upload size or checksum mismatch")
            spool.seek(0)
            metadata = ObjectMetadata(size, object_id)
            created = await self.backend.put(key, spool, metadata)
            if not created and await self.backend.head(key) != metadata:
                raise ObjectUnavailable("Conflicting stored object")
            return created

    async def head(self, object_id: str) -> ObjectMetadata | None:
        value = await self.backend.head(object_key(self.tenant, object_id))
        if value is not None and (type(value.size) is not int or not 1 <= value.size <= MAX_OBJECT_BYTES or value.sha256 != object_id):
            raise ObjectUnavailable("Invalid stored object metadata")
        return value

    async def read(self, object_id: str, start: int, length: int) -> AsyncIterator[bytes]:
        if type(start) is not int or type(length) is not int or start < 0 or length < 1 or start + length > MAX_OBJECT_BYTES:
            raise ObjectInputError("Invalid read bounds")
        remaining = length
        async with aclosing(self.backend.read(object_key(self.tenant, object_id), start, length)) as stream:
            async for chunk in stream:
                if not isinstance(chunk, bytes) or len(chunk) > remaining:
                    raise ObjectUnavailable("Invalid provider response")
                remaining -= len(chunk)
                yield chunk
        if remaining:
            raise ObjectUnavailable("Truncated provider response")

    async def delete(self, object_id: str) -> None:
        await self.backend.delete(object_key(self.tenant, object_id))
