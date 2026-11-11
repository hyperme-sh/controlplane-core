"""Private Azure backend owned and configured by the authenticated tenant."""
from typing import AsyncIterator, BinaryIO

from azure.core.exceptions import ResourceExistsError, ResourceNotFoundError
from azure.storage.blob import ContentSettings

from api.domain.tenant_objects import ObjectBackend, ObjectMetadata, ObjectUnavailable


class AzureTenantObjects(ObjectBackend):
    def __init__(self, container):
        self.container = container

    async def put(self, key: str, body: BinaryIO, metadata: ObjectMetadata) -> bool:
        try:
            await self.container.get_blob_client(key).upload_blob(body, length=metadata.size,
                blob_type="BlockBlob", overwrite=False, metadata={"sha256": metadata.sha256},
                content_settings=ContentSettings(content_type="application/octet-stream", cache_control="private, no-store"),
                validate_content=True, max_concurrency=1)
            return True
        except ResourceExistsError as exc:
            if exc.error_code == "BlobAlreadyExists":
                return False
            raise ObjectUnavailable("Tenant storage write failed") from exc
        except Exception as exc:
            raise ObjectUnavailable("Tenant storage write failed") from exc

    async def head(self, key: str) -> ObjectMetadata | None:
        try:
            props = await self.container.get_blob_client(key).get_blob_properties()
            return ObjectMetadata(props.size, props.metadata.get("sha256", ""))
        except ResourceNotFoundError as exc:
            if exc.error_code == "BlobNotFound":
                return None
            raise ObjectUnavailable("Tenant storage unavailable") from exc
        except Exception as exc:
            raise ObjectUnavailable("Tenant storage read failed") from exc

    async def read(self, key: str, start: int, length: int) -> AsyncIterator[bytes]:
        try:
            download = await self.container.get_blob_client(key).download_blob(offset=start, length=length, max_concurrency=1)
            if download.size != length:
                raise ObjectUnavailable("Invalid provider range response")
            async for chunk in download.chunks():
                yield chunk
        except Exception as exc:
            raise ObjectUnavailable("Tenant storage read failed") from exc

    async def delete(self, key: str) -> None:
        try:
            await self.container.get_blob_client(key).delete_blob()
        except ResourceNotFoundError as exc:
            if exc.error_code != "BlobNotFound":
                raise ObjectUnavailable("Tenant storage unavailable") from exc
        except Exception as exc:
            raise ObjectUnavailable("Tenant storage delete failed") from exc
