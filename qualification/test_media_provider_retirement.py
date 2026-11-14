"""CI-only Azurite contract for current payload removal and delayed writes."""
import base64
import hashlib
from io import BytesIO
import os
from uuid import uuid4
import pytest
from azure.core import MatchConditions
from azure.core.exceptions import HttpResponseError
from azure.storage.blob import BlobBlock
from azure.storage.blob.aio import BlobServiceClient
from api.domain.tenant_objects import ObjectMetadata
from api.infrastructure.externals.media_retirement_backend import AzureMediaObjects,media_key,RETIRED


@pytest.mark.asyncio
async def test_azure_retirement_blocks_late_single_and_block_uploads():
    account='s3rchfixture'
    secret=base64.b64encode(b'0'*32).decode()
    origin=os.environ['AZURITE_BLOB_ORIGIN']
    async with BlobServiceClient(account_url=origin+'/'+account,credential={'account_name':account,'account_key':secret},api_version='2023-11-03') as service:
        container=service.get_container_client('retirement-'+uuid4().hex)
        await container.create_container()
        try:
            backend=AzureMediaObjects(container)
            data=b'opaque fixture ciphertext '*4
            digest=hashlib.sha256(data).hexdigest()
            key=media_key(uuid4(),'ciphertext',digest)
            assert await backend.put(key,BytesIO(data),ObjectMetadata(len(data),digest))
            await backend.retire(key)
            assert await backend.head(key)==RETIRED
            assert not await backend.put(key,BytesIO(data),ObjectMetadata(len(data),digest))
            await backend.retire(key)
            assert await (await container.get_blob_client(key).download_blob()).readall()==b''
            # A block uploaded before retirement must not commit after it.
            control=container.get_blob_client(media_key(uuid4(),'ciphertext',digest))
            control_block=base64.b64encode(b'control-block-id').decode()
            await control.stage_block(block_id=control_block,data=data,length=len(data))
            await control.commit_block_list([BlobBlock(block_id=control_block)],match_condition=MatchConditions.IfMissing,metadata={'sha256':digest})
            assert await (await control.download_blob()).readall()==data
            pending=media_key(uuid4(),'ciphertext',digest)
            blob=container.get_blob_client(pending)
            block=base64.b64encode(b'fixed-block-id').decode()
            await blob.stage_block(block_id=block,data=data,length=len(data))
            await backend.retire(pending)
            with pytest.raises(HttpResponseError) as failure:
                await blob.commit_block_list([BlobBlock(block_id=block)],match_condition=MatchConditions.IfMissing,metadata={'sha256':digest})
            assert failure.value.status_code in (400,409,412)
            assert failure.value.error_code in ('ConditionNotMet','BlobAlreadyExists','InvalidBlockList')
            assert await backend.head(pending)==RETIRED
            assert await (await blob.download_blob()).readall()==b''
        finally:
            await container.delete_container()
