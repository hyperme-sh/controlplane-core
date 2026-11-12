"""Tenant object service access from the gateway's bounded API-key token."""
import time
from uuid import UUID

from fastapi import HTTPException
from api.infrastructure.security.api_key_exchange_jwt import try_decode_api_key_session_jwt
from api.domain.storage_scope import storage_tenant

SCOPES = {"read": "storage:objects:read", "write": "storage:objects:write", "delete": "storage:objects:delete"}
SCOPES.update({"records." + operation: "storage:records:" + operation for operation in ("read", "write", "delete")})


def request_operation(method: str, path: str) -> str | None:
    operation = {"GET": "read", "HEAD": "read", "PUT": "write", "DELETE": "delete"}.get(method)
    if operation and path.startswith("/api/v1/storage/records/"):
        return "records." + operation
    return operation


def storage_session(token: str, operation: str, now: int | None = None) -> UUID:
    if operation not in SCOPES or not isinstance(token, str) or not 1 <= len(token) <= 16384:
        raise HTTPException(status_code=401, detail="Storage authentication required")
    claims = try_decode_api_key_session_jwt(token)
    try:
        if not claims or claims.get("azp") != "controlplane-api-key":
            raise ValueError()
        at = int(time.time()) if now is None else now
        issued, expires = claims.get("iat"), claims.get("exp")
        if type(issued) is not int or type(expires) is not int or not 0 <= issued <= at < expires or expires - issued > 300:
            raise ValueError()
        subject = claims.get("sub")
        if not isinstance(subject, str) or not subject.startswith("apikey:") or str(UUID(subject[7:])) != subject[7:]:
            raise ValueError()
        value = claims.get("https://spanopticon.dev/tenant_id")
        tenant = storage_tenant(value)
        if str(tenant) != value:
            raise ValueError()
        scope = claims.get("scope")
        if not isinstance(scope, str) or len(scope) > 4096 or SCOPES[operation] not in scope.split():
            raise HTTPException(status_code=403, detail="Storage scope required")
        return tenant
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail="Invalid storage authentication") from exc
