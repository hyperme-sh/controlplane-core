"""Independent retirement wire vectors and source-key separation."""
import base64
from dataclasses import replace
import json
from uuid import UUID
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from api.domain.media_allocation_grant import AllocationRejected
from api.domain.media_retirement_request import RetirementScope, RetirementRequestSigner, verify_retirement_request
from test_media_allocation_grant import grant


@pytest.fixture
def retirement(grant):
    base=grant[0]
    scope=RetirementScope(base.issuer,base.tenant,base.source,base.object,base.owner,'e'*64)
    key=Ed25519PrivateKey.from_private_bytes(b'\x08'*32)
    signer=RetirementRequestSigner(issuer=scope.issuer,tenant=scope.tenant,source=scope.source,key_id='source',signing_key=key)
    claims={**scope.claims(),'version':1,'issuedAt':1000,'expiresAt':1030}
    def encode(changes=None,*,domain=b'spanopticon-media-retirement-v1\x00',envelope_changes=None,key_id='source',signing_key=key):
        value={**claims,**(changes or {})}
        message=domain+json.dumps({'keyId':key_id,'claims':value},sort_keys=True,separators=(',',':'),ensure_ascii=True).encode('ascii')
        signature=base64.urlsafe_b64encode(signing_key.sign(message)).rstrip(b'=').decode()
        return json.dumps({'keyId':key_id,'claims':value,'signature':signature,**(envelope_changes or {})}).encode()
    def verify(raw=None,**changes):
        return verify_retirement_request(encode() if raw is None else raw,**{'expected':scope,'keys':{'source':key.public_key()},'now':1000,**changes})
    return scope,key,signer,encode,verify


def test_independent_vector_signer_and_half_open_transport_window(retirement):
    scope,key,signer,encode,verify=retirement
    signed=signer.sign(object=scope.object,owner=scope.owner,command=scope.command,now=1000)
    assert json.loads(signed)==json.loads(encode())
    assert verify(signed).scope==scope
    assert verify(now=1029).expires_at==1030
    for now in [999,1030,True,-1]:
        with pytest.raises(AllocationRejected):verify(now=now)
    retry=signer.sign(object=scope.object,owner=scope.owner,command=scope.command,now=2000)
    assert verify(retry,now=2001).scope==scope
    with pytest.raises(AllocationRejected):signer.sign(object=scope.object,owner=scope.owner,command=scope.command,now=2**53-1)


@pytest.mark.parametrize('field,value', [('issuer',str(UUID(int=99))),('tenant',str(UUID(int=99))),('source',str(UUID(int=99))),('object','media_p_bad'),('owner','bad'),('command','f'*64),('version',True),('version',2),('issuedAt',True),('expiresAt',1000),('expiresAt',1031)])
def test_every_bound_field_is_checked(retirement,field,value):
    _,_,_,encode,verify=retirement
    with pytest.raises(AllocationRejected):verify(encode({field:value}))


def test_domain_key_signature_encoding_and_json_refusals(retirement):
    _,_,_,encode,verify=retirement
    wrong=Ed25519PrivateKey.from_private_bytes(b'\x09'*32)
    for raw in [b'',b'x'*4097,b'\xff',b'{',b'['*1500,b'[]',b'{"keyId":1,"keyId":2}',
                encode({'extra':1}),encode(domain=b'spanopticon-media-allocation-request-v1\x00'),
                encode(key_id='other'),encode(key_id='!'),encode(signing_key=wrong),
                encode(envelope_changes={'signature':'bad'}),encode(envelope_changes={'extra':1}),
                encode(envelope_changes={'claims':[]}),encode(envelope_changes={'keyId':3})]:
        with pytest.raises(AllocationRejected):verify(raw)
    for keys in [{},{str(i):wrong.public_key() for i in range(17)},{'source':object()}]:
        with pytest.raises(AllocationRejected):verify(keys=keys)


def test_scope_and_signer_configuration_refuse_malformed_inputs(retirement):
    scope,key,_,_,_=retirement
    for field,value in [('tenant','bad'),('issuer',str(UUID(int=0))),('source',None),('command','E'*64),('command',None),('owner','bad'),('owner','a'*43+'.'+'b'*43),('owner','!'*43+'.'+'b'*43),('object',scope.object[:-1]+'z')]:
        with pytest.raises(AllocationRejected):replace(scope,**{field:value}).claims()
    for kid,private in [('!',key),(None,key),('source',object())]:
        with pytest.raises(AllocationRejected):RetirementRequestSigner(issuer=scope.issuer,tenant=scope.tenant,source=scope.source,key_id=kid,signing_key=private)
