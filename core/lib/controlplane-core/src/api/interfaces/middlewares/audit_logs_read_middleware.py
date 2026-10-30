from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from controlplane_api.utils import capture_audit_log, get_logger
from api.core.container import get_role_service, get_subscription_plan_service, get_user_service
from controlplane_api.dtos.audit import AuditLogDto
from api.infrastructure.security.current_user import resolve_me_optional_from_token

logger = get_logger(__name__)

class AuditLogsReadMiddleware(BaseHTTPMiddleware):
    def _get_entity_name(self, path: str) -> str:
        if "/api/v1/users" in path:
            return "User"
        elif "/api/v1/roles" in path:
            return "Role"
        elif "/api/v1/tenants" in path:
            return "Tenant"
  
        return "Unknown"
    async def dispatch(self, request: Request, call_next):
        token = None
        if "authorization" in request.headers:
            token = request.headers["authorization"].replace("Bearer ", "")
        user_service = get_user_service()
        role_service = get_role_service()
        subscription_service = get_subscription_plan_service()
        if request.method == "GET":
            current_user = await resolve_me_optional_from_token(
                token,
                user_service=user_service,
                role_service=role_service,
                subscription_service=subscription_service,
            )
        
            entity = self._get_entity_name(request.url.path)
            await capture_audit_log(AuditLogDto(
                action="read",
                entity=entity if entity != "Unknown" else "AuditLog",
                user_id=str(current_user.id) if current_user else "Anonymous",
                changes={"info": f"Read access to {request.url.path}"},
                tenant_id=str(current_user.tenant_id) if current_user and current_user.tenant_id else None
            ))
    
        
        return await call_next(request)
        