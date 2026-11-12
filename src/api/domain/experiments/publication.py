"""Tenant-bound signed publication through the existing read-only cache contract."""
import base64
import hashlib
import json
import secrets
from typing import Literal
from uuid import UUID

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from pydantic import Field

from api.domain.experiments.control import ClosedModel, Second, signed_payload
from api.domain.experiments.store import ExperimentStore
from api.domain.tenant_records import MAGIC, RecordBackend, RecordConflict, TenantRecords, decode, provider_etag
from api.domain.storage_scope import storage_tenant


class Publication(ClosedModel):
    tenant: UUID
    audience: Literal["s3r.ch", "hyperme.sh", "portal"]
    sequence: int = Field(ge=1, le=9007199254740991)
    config_sequence: int = Field(ge=1, le=9007199254740991)
    policy_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    issued: Second
    expires: Second
    payload: str = Field(max_length=32768)
    signature: str = Field(pattern=r"^[A-Za-z0-9_-]{86}$")

    def envelope(self) -> bytes:
        return json.dumps({"payload": self.payload, "signature": self.signature}, separators=(",", ":")).encode()


class Publisher:
    def __init__(self, tenant: UUID, audience: str, key: Ed25519PrivateKey, backend: RecordBackend):
        self.tenant = storage_tenant(tenant)
        if audience not in ("s3r.ch", "hyperme.sh", "portal") or not isinstance(key, Ed25519PrivateKey):
            raise ValueError("explicit experiment signing binding required")
        self.audience, self.key, self.backend = audience, key, backend
        self.store = ExperimentStore(self.tenant, backend)
        self.records = TenantRecords(self.tenant, backend)
        self.state_key = f"tenant-experiment-publications/v1/{self.tenant.hex}/{audience}"
        self.cache_name = "experiments-v1-" + {"s3r.ch": "s3rch", "hyperme.sh": "hypermesh", "portal": "portal"}[audience]

    def verified_sequence(self, data: bytes) -> int | None:
        # The public cache is not authoritative. Even a cache writer cannot
        # nominate a sequence without a signature from this deployment's key.
        try:
            if len(data) > 65536:
                return None
            envelope = json.loads(data)
            if set(envelope) != {"payload", "signature"}:
                return None
            raw, signature = envelope["payload"].encode(), envelope["signature"]
            if len(raw) > 32768 or len(signature) != 86:
                return None
            self.key.public_key().verify(base64.urlsafe_b64decode(signature + "=="), raw)
            value = json.loads(raw)
            sequence = value["sequence"]
            if value.get("audience") == self.audience and type(sequence) is int and 1 <= sequence <= 9007199254740991:
                return sequence
        except Exception:
            pass
        return None

    async def publish(self, now: int, *, ttl: int = 300) -> bool:
        if type(now) is not int or not 0 <= now <= 9007199254740691:
            raise ValueError("invalid publication time")
        if type(ttl) is not int or not 1 <= ttl <= 300:
            raise ValueError('invalid publication stale limit')
        config = await self.store.load(self.tenant, self.audience)
        policy_hash = hashlib.sha256(config.model_dump_json().encode()).hexdigest()
        found = await self.backend.fetch(self.state_key)
        previous = None
        etag = None
        if found is not None:
            etag = provider_etag(found[1])
            if len(found[0]) > 65536:
                raise ValueError("invalid private publication")
            previous = Publication.model_validate_json(decode(found[0]).data)
            if (previous.tenant != self.tenant or previous.audience != self.audience
                    or config.sequence < previous.config_sequence or now < previous.issued
                    or self.verified_sequence(previous.envelope()) != previous.sequence):
                raise ValueError("publication scope or sequence mismatch")
        if (previous is not None and previous.config_sequence == config.sequence
                and previous.policy_hash != policy_hash):
            raise ValueError("configuration changed without a sequence")
        publication = previous
        if previous is None or previous.config_sequence != config.sequence or previous.expires <= now + min(120, ttl // 2) or previous.expires > now + ttl:
            sequence = 1 if previous is None else previous.sequence + 1
            # Publication renewal has its own sequence and does not invalidate
            # an operator's conditional policy edit.
            raw, signature = signed_payload(config.model_copy(update={"sequence": sequence}), now=now, ttl=ttl, sign=self.key.sign)
            publication = Publication(tenant=self.tenant, audience=self.audience, sequence=sequence,
                config_sequence=config.sequence, policy_hash=policy_hash, issued=now, expires=now+ttl,
                payload=raw.decode(), signature=signature)
            if await self.store.load(self.tenant, self.audience) != config:
                raise RecordConflict("configuration changed before publication")
            await self.backend.replace(self.state_key, MAGIC + secrets.token_bytes(32) + publication.model_dump_json().encode(), etag)
        data = publication.envelope()
        cached = await self.records.get("cache", self.cache_name)
        if cached is not None:
            if cached.data == data:
                return False
            sequence = self.verified_sequence(cached.data)
            if sequence is not None and sequence >= publication.sequence:
                raise RecordConflict("newer signed publication already cached")
        await self.records.put("cache", self.cache_name, data, cached.revision if cached else None)
        return True
