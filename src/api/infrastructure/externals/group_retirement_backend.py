"""Private group provider tombstones prevent late create-only uploads reviving bytes."""
from contextlib import aclosing
import hashlib
import json
import re
from uuid import UUID
import httpx
from azure.storage.blob import ContentSettings
from api.domain.tenant_objects import ObjectInputError, ObjectMetadata, ObjectUnavailable
from api.infrastructure.externals.tenant_object_azure import AzureTenantObjects
from api.infrastructure.externals.tenant_object_s3 import S3TenantObjects

EMPTY_DIGEST=hashlib.sha256(b'').hexdigest()
RETIRED=ObjectMetadata(0,EMPTY_DIGEST)


def group_key(tenant,digest):
    if not isinstance(tenant,UUID) or not tenant.int or type(digest) is not str or not re.fullmatch('[0-9a-f]{64}',digest):
        raise ObjectInputError('Invalid group storage key')
    return f'tenant-group/v1/{tenant.hex}/wrap/{digest}'


def checked(key):
    if type(key) is not str:
        raise ObjectInputError('Invalid group storage key')
    match=re.fullmatch(r'tenant-group/v1/([0-9a-f]{32})/(wrap)/([0-9a-f]{64})',key)
    if not match or not UUID(hex=match[1]).int:
        raise ObjectInputError('Invalid group storage key')
    return match[2],match[3]


class GroupNamespace:
    """No generic object key or hard deletion can erase a permanent marker."""
    async def put(self,key,body,metadata):
        _,digest=checked(key)
        maximum=65536
        minimum=1
        if type(metadata.size) is not int or not minimum<=metadata.size<=maximum or metadata.sha256!=digest:
            raise ObjectInputError('Invalid group object metadata')
        return await super().put(key,body,metadata)

    async def head(self,key):
        checked(key)
        return await super().head(key)

    async def read(self,key,start,length):
        checked(key)
        async with aclosing(super().read(key,start,length)) as stream:
            async for chunk in stream:yield chunk

    async def delete(self,key):
        raise ObjectInputError('Group requires permanent retirement')


class AzureGroupObjects(GroupNamespace,AzureTenantObjects):
    async def retire(self,key):
        checked(key)
        if await self.head(key)==RETIRED:return
        try:
            await self.container.get_blob_client(key).upload_blob(b'',length=0,blob_type='BlockBlob',overwrite=True,
                metadata={'sha256':EMPTY_DIGEST,'retired':'1'},
                content_settings=ContentSettings(content_type='application/octet-stream',cache_control='private, no-store'),
                validate_content=True,max_concurrency=1)
        except Exception as exc:
            raise ObjectUnavailable('Group retirement failed') from exc
        if await self.head(key)!=RETIRED:raise ObjectUnavailable('Group retirement not confirmed')


class S3GroupObjects(GroupNamespace,S3TenantObjects):
    async def retire(self,key):
        checked(key)
        if await self.head(key)==RETIRED:return
        headers={'Content-Length':'0','Content-Type':'application/octet-stream','Cache-Control':'private, no-store',
                 'x-amz-content-sha256':EMPTY_DIGEST,'x-amz-meta-sha256':EMPTY_DIGEST,'x-amz-meta-retired':'1'}
        url,headers=self._signed('PUT',key,headers)
        try:
            async with httpx.AsyncClient(timeout=30,follow_redirects=False,trust_env=False) as client:
                async with client.stream('PUT',url,headers=headers,content=b'') as response:
                    if response.status_code!=200:raise ObjectUnavailable('Group retirement failed')
        except Exception as exc:
            raise ObjectUnavailable('Group retirement failed') from exc
        if await self.head(key)!=RETIRED:raise ObjectUnavailable('Group retirement not confirmed')


def provider_location(backend):
    """Stable namespace identity excludes credentials so rotation stays compatible."""
    if isinstance(backend,AzureTenantObjects):
        parts=['azure',backend.container.url]
    elif isinstance(backend,S3TenantObjects):
        parts=['s3',backend.client.meta.endpoint_url,backend.bucket]
    else:raise ObjectInputError('Group provider location unavailable')
    if any(type(part) is not str or not part or len(part)>2048 for part in parts):
        raise ObjectInputError('Group provider location unavailable')
    return hashlib.sha256(json.dumps(parts,separators=(',',':')).encode()).hexdigest()
