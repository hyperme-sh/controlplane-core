"""Conditional experiment state in a private provider prefix, outside public records."""
import secrets
from collections.abc import Callable
from uuid import UUID

from api.domain.experiments.control import Configuration
from api.domain.tenant_records import MAGIC, RecordBackend, RecordConflict, decode, provider_etag
from api.domain.storage_scope import storage_tenant


class ExperimentStore:
    def __init__(self, tenant: UUID, backend: RecordBackend):
        self.tenant = storage_tenant(tenant)
        self.backend = backend

    def key(self, tenant: UUID, audience: str) -> str:
        if tenant != self.tenant or audience not in ("s3r.ch", "hyperme.sh", "portal"):
            raise ValueError("experiment store scope mismatch")
        # General storage:records credentials cannot address this prefix.
        return f"tenant-experiments/v1/{self.tenant.hex}/{audience}"

    def parse(self, raw: bytes, tenant: UUID, audience: str) -> Configuration:
        if len(raw) > 32768:
            raise ValueError("experiment configuration exceeds limit")
        value = Configuration.model_validate_json(decode(raw).data)
        if value.tenant != tenant or value.audience != audience:
            raise ValueError("experiment store scope mismatch")
        return value

    def encode(self, value: Configuration) -> bytes:
        raw = MAGIC + secrets.token_bytes(32) + value.model_dump_json().encode()
        self.parse(raw, self.tenant, value.audience)
        return raw

    async def load(self, tenant: UUID, audience: str) -> Configuration:
        found = await self.backend.fetch(self.key(tenant, audience))
        if found is None:
            raise ValueError("experiment configuration unavailable")
        provider_etag(found[1])
        return self.parse(found[0], tenant, audience)

    async def initialize(self, value: Configuration) -> None:
        key = self.key(value.tenant, value.audience)
        if value.sequence != 1:
            raise ValueError("initial experiment sequence must be one")
        await self.backend.replace(key, self.encode(value), None)

    async def replace(self, previous: Configuration, next_config: Configuration,
                      *, before_write: Callable[[], None] | None = None) -> bool:
        key = self.key(previous.tenant, previous.audience)
        if (next_config.tenant != previous.tenant or next_config.audience != previous.audience
                or next_config.sequence != previous.sequence + 1):
            raise ValueError("invalid experiment configuration transition")
        found = await self.backend.fetch(key)
        if found is None:
            return False
        if self.parse(found[0], previous.tenant, previous.audience) != previous:
            return False
        etag = provider_etag(found[1])
        encoded = self.encode(next_config)
        # The conditional read can block after the controller's first check.
        # Validate immediately before dispatch; network completion may be later.
        if before_write is not None:
            before_write()
        try:
            await self.backend.replace(key, encoded, etag)
        except RecordConflict:
            return False
        return True
