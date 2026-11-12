"""Private S3 conditional records using the owner's explicit signing settings."""
import hashlib

import httpx

from api.domain.tenant_objects import ObjectUnavailable
from api.domain.tenant_records import MAX_STORED_BYTES, RecordConflict, provider_etag


class S3TenantRecords:
    def __init__(self, objects):
        self.objects = objects

    async def fetch(self, key):
        url, headers = self.objects._signed("GET", key, {"x-amz-content-sha256": hashlib.sha256(b"").hexdigest(), "Accept-Encoding": "identity"})
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
                async with client.stream("GET", url, headers=headers) as response:
                    if response.status_code == 404:
                        return None
                    if response.status_code != 200 or response.headers.get("content-encoding", "identity") != "identity":
                        raise ObjectUnavailable("Record storage unavailable")
                    etag = provider_etag(response.headers.get("etag"))
                    size = int(response.headers["content-length"])
                    if not 1 <= size <= MAX_STORED_BYTES:
                        raise ObjectUnavailable("Invalid stored record size")
                    data = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(data) + len(chunk) > size:
                            raise ObjectUnavailable("Invalid stored record length")
                        data.extend(chunk)
                    if len(data) != size:
                        raise ObjectUnavailable("Invalid stored record length")
                    return bytes(data), etag
        except ObjectUnavailable:
            raise
        except Exception:
            raise ObjectUnavailable("Record storage unavailable") from None

    async def write(self, method, key, data, etag):
        headers = {"x-amz-content-sha256": hashlib.sha256(data).hexdigest(), "Content-Length": str(len(data)),
            "Content-Type": "application/octet-stream", "Cache-Control": "private, no-store"}
        headers.update({"If-None-Match": "*"} if etag is None else {"If-Match": provider_etag(etag)})
        url, headers = self.objects._signed(method, key, headers)
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=False, trust_env=False) as client:
                async with client.stream(method, url, headers=headers, content=data) as response:
                    if response.status_code in (409, 412) or (method == "DELETE" and response.status_code == 404):
                        raise RecordConflict("Record changed")
                    if response.status_code != (200 if method == "PUT" else 204):
                        raise ObjectUnavailable("Record storage unavailable")
        except (ObjectUnavailable, RecordConflict):
            raise
        except Exception:
            raise ObjectUnavailable("Record storage unavailable") from None

    async def replace(self, key, data, etag):
        await self.write("PUT", key, data, etag)

    async def remove(self, key, etag):
        await self.write("DELETE", key, b"", provider_etag(etag))
