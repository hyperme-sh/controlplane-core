"""Conditional Azure record operations with bounded, consistent reads."""
from azure.core import MatchConditions
from azure.core.exceptions import ResourceExistsError, ResourceModifiedError, ResourceNotFoundError
from azure.storage.blob import ContentSettings

from api.domain.tenant_objects import ObjectUnavailable
from api.domain.tenant_records import MAX_STORED_BYTES, RecordConflict, provider_etag


class AzureTenantRecords:
    def __init__(self, container):
        self.container = container

    async def fetch(self, key):
        try:
            blob = self.container.get_blob_client(key)
            props = await blob.get_blob_properties()
            etag = provider_etag(props.etag)
            if type(props.size) is not int or not 1 <= props.size <= MAX_STORED_BYTES:
                raise ObjectUnavailable("Invalid stored record size")
            download = await blob.download_blob(etag=etag, match_condition=MatchConditions.IfNotModified, max_concurrency=1)
            data = bytearray()
            async for chunk in download.chunks():
                if len(data) + len(chunk) > props.size:
                    raise ObjectUnavailable("Invalid stored record length")
                data.extend(chunk)
            if len(data) != props.size:
                raise ObjectUnavailable("Invalid stored record length")
            return bytes(data), etag
        except ResourceNotFoundError as exc:
            if exc.error_code == "BlobNotFound":
                return None
            raise ObjectUnavailable("Record storage unavailable") from None
        except ResourceModifiedError:
            raise RecordConflict("Record changed") from None
        except (ObjectUnavailable, RecordConflict):
            raise
        except Exception:
            raise ObjectUnavailable("Record storage unavailable") from None

    async def replace(self, key, data, etag):
        try:
            conditions = {} if etag is None else {"etag": provider_etag(etag), "match_condition": MatchConditions.IfNotModified}
            await self.container.get_blob_client(key).upload_blob(data, length=len(data), overwrite=etag is not None,
                blob_type="BlockBlob", validate_content=True, max_concurrency=1,
                content_settings=ContentSettings(content_type="application/octet-stream", cache_control="private, no-store"),
                **conditions)
        except ResourceExistsError as exc:
            if exc.error_code != "BlobAlreadyExists":
                raise ObjectUnavailable("Record storage unavailable") from None
            raise RecordConflict("Record changed") from None
        except ResourceModifiedError:
            raise RecordConflict("Record changed") from None
        except Exception:
            raise ObjectUnavailable("Record storage unavailable") from None

    async def remove(self, key, etag):
        try:
            await self.container.get_blob_client(key).delete_blob(etag=provider_etag(etag), match_condition=MatchConditions.IfNotModified)
        except ResourceNotFoundError as exc:
            if exc.error_code != "BlobNotFound":
                raise ObjectUnavailable("Record storage unavailable") from None
            raise RecordConflict("Record changed") from None
        except ResourceModifiedError:
            raise RecordConflict("Record changed") from None
        except Exception:
            raise ObjectUnavailable("Record storage unavailable") from None
