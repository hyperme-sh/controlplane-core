"""S3 adapter for the explicit tenant object protocol; no global defaults."""
from __future__ import annotations

import base64
import hashlib
from urllib.parse import quote

import httpx
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.credentials import Credentials
from functools import partial
from typing import AsyncIterator, BinaryIO

import anyio
from botocore.exceptions import ClientError

from api.domain.tenant_objects import CHUNK_BYTES, ObjectBackend, ObjectMetadata, ObjectUnavailable


class S3TenantObjects(ObjectBackend):
    def __init__(self, client, bucket: str, *, region: str = "", access_key: str = "", secret_key: str = ""):
        if not isinstance(bucket, str) or not bucket or "/" in bucket:
            raise ObjectUnavailable("Invalid tenant storage configuration")
        self.client, self.bucket = client, bucket
        self.region = region
        self.credentials = Credentials(access_key, secret_key)

    def _signed(self, method: str, key: str, headers: dict):
        # Only explicit tenant credentials; never consult a credential provider.
        if not self.region or not self.credentials.access_key or not self.credentials.secret_key:
            raise ObjectUnavailable("Invalid tenant storage configuration")
        endpoint = self.client.meta.endpoint_url
        if not endpoint.startswith("https://"):
            raise ObjectUnavailable("Private HTTPS storage required")
        url = f"{endpoint.rstrip('/')}/{quote(self.bucket, safe='')}/{quote(key, safe='/')}"
        request = AWSRequest(method=method, url=url, headers=headers)
        SigV4Auth(self.credentials, "s3", self.region).add_auth(request)
        return url, dict(request.headers.items())

    async def put(self, key: str, body: BinaryIO, metadata: ObjectMetadata) -> bool:
        headers = {"Content-Length": str(metadata.size), "Content-Type": "application/octet-stream",
            "Cache-Control": "private, no-store", "If-None-Match": "*",
            "x-amz-checksum-sha256": base64.b64encode(bytes.fromhex(metadata.sha256)).decode("ascii"),
            "x-amz-content-sha256": metadata.sha256, "x-amz-meta-sha256": metadata.sha256}
        url, headers = self._signed("PUT", key, headers)
        async def content():
            while chunk := body.read(CHUNK_BYTES):
                yield chunk
                await anyio.lowlevel.checkpoint()
        # Async network IO stays inside the caller's deadline. Cancellation closes
        # the connection before the caller closes its spool; no worker owns it.
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            async with client.stream("PUT", url, headers=headers, content=content()) as response:
                if response.status_code == 412:
                    return False
                if response.status_code != 200:
                    raise ObjectUnavailable("Tenant storage write failed")
                return True

    async def head(self, key: str) -> ObjectMetadata | None:
        url, headers = self._signed("HEAD", key, {"x-amz-content-sha256": hashlib.sha256(b"").hexdigest()})
        async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
            async with client.stream("HEAD", url, headers=headers) as response:
                if response.status_code == 404:
                    return None
                if response.status_code != 200:
                    raise ObjectUnavailable("Tenant storage read failed")
                try:
                    return ObjectMetadata(int(response.headers["content-length"]), response.headers.get("x-amz-meta-sha256", ""))
                except (KeyError, ValueError) as exc:
                    raise ObjectUnavailable("Invalid stored object metadata") from exc

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
