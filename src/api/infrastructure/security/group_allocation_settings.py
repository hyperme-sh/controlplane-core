"""Explicit private issuer bindings; absence leaves allocation transport disabled."""
from dataclasses import dataclass
import os
import time

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from api.domain.group_allocation_grant import AllocationRejected, _b64
from api.domain.group_allocation_issuer import GroupAllocationIssuer
from api.domain.group_allocation_store import GroupAllocationStore
from api.storage.configure_provider import private_json


@dataclass(frozen=True)
class IssuerSettings:
    tenant: str
    issuer: str
    source: str
    policy_digest: str
    policy_until: int
    key_id: str
    signing_key: Ed25519PrivateKey
    request_keys: dict

    def bind(self, backend, clock=lambda:int(time.time())):
        return GroupAllocationIssuer(GroupAllocationStore(self.tenant,self.issuer,backend),source=self.source,
            policy_digest=self.policy_digest,policy_until=self.policy_until,request_keys=self.request_keys,
            key_id=self.key_id,signing_key=self.signing_key,clock=clock)


def load_issuers(environ=None):
    path=(os.environ if environ is None else environ).get('STORAGE_GROUP_ALLOCATION_FILE')
    if path is None:
        return {}
    try:
        value=private_json(path)
        if (set(value)!={'version','bindings'} or type(value['version']) is not int or value['version'] != 1
                or type(value['bindings']) is not list or not 1<=len(value['bindings'])<=8):
            raise ValueError()
        result={}
        fields={'tenant','issuer','source','policyDigest','policyUntil','keyId','signingKey','requestKeys'}
        for row in value['bindings']:
            if type(row) is not dict or set(row)!=fields:
                raise ValueError()
            if type(row['requestKeys']) is not dict:
                raise ValueError()
            settings=IssuerSettings(row['tenant'],row['issuer'],row['source'],row['policyDigest'],row['policyUntil'],row['keyId'],
                Ed25519PrivateKey.from_private_bytes(_b64(row['signingKey'],32)),
                {kid:Ed25519PublicKey.from_public_bytes(_b64(key,32)) for kid,key in row['requestKeys'].items()})
            settings.bind(None)  # Structural checks only; no provider call or bootstrap.
            if settings.tenant in result:
                raise ValueError()
            result[settings.tenant]=settings
        return result
    except Exception:
        raise ValueError('Invalid private group allocation configuration') from None
