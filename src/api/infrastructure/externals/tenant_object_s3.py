"""S3 adapter for the explicit tenant object protocol; no global defaults."""
from __future__ import annotations

import base64
from functools import partial
from typing import AsyncIterator, BinaryIO

import anyio
from botocore.exceptions import ClientError

from api.domain.tenant_objects import CHUNK_BYTES, ObjectBackend, ObjectMetadata, ObjectUnavailable


class S3TenantObjects(ObjectBackend):
    def __init__(self, client, bucket: str):
        if not isinstance(bucket, str) or not bucket or "/" in bucket:
            raise ObjectUnavailable("Invalid tenant storage configuration")
        self.client, self.bucket = client, bucket

    async def put(self, key: str, body: BinaryIO, metadata: ObjectMetadata) -> bool:
        try:
            await anyio.to_thread.run_sync(partial(self.client.put_object, Bucket=self.bucket, Key=key,
                Body=body, ContentLength=metadata.size, ContentType="application/octet-stream",
                CacheControl="private, no-store", IfNoneMatch="*",
                ChecksumSHA256=base64.b64encode(bytes.fromhex(metadata.sha256)).decode("ascii"),
                Metadata={"sha256": metadata.sha256}))
            return True
        except ClientError as exc:
            if exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode") == 412:
                return False
            raise ObjectUnavailable("Tenant storage write failed") from exc

    async def head(self, key: str) -> ObjectMetadata | None:
        try:
            value = await anyio.to_thread.run_sync(partial(self.client.head_object, Bucket=self.bucket, Key=key))
            return ObjectMetadata(value["ContentLength"], value.get("Metadata", {}).get("sha256", ""))
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404", "NotFound"):
                return None
            raise ObjectUnavailable("Tenant storage read failed") from exc

    async def read(self, key: str, start: int, length: int) -> AsyncIterator[bytes]:
        response = await anyio.to_thread.run_sync(partial(self.client.get_object, Bucket=self.bucket, Key=key,
            Range=f"bytes={start}-{start + length - 1}"))
        stream = response["Body"]
        try:
            if response.get("ContentLength") != length or not response.get("ContentRange", "").startswith(f"bytes {start}-{start + length - 1}/"):
                raise ObjectUnavailable("Invalid provider range response")
            while chunk := await anyio.to_thread.run_sync(stream.read, min(CHUNK_BYTES, length)):
                yield chunk
        finally:
            stream.close()

    async def delete(self, key: str) -> None:
        try:
            await anyio.to_thread.run_sync(partial(self.client.delete_object, Bucket=self.bucket, Key=key))
        except ClientError as exc:
            raise ObjectUnavailable("Tenant storage delete failed") from exc
