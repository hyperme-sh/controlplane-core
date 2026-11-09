from fastapi import Request
from api.core.container import get_tenant_service
from controlplane_api.utils import get_logger
from api.core.exceptions import FeatureNotEnabledException
from api.domain.dtos.role_dto import RoleDto
from api.domain.enum.feature import Feature as FeatureEnum
from controlplane_api.permissions import Permission
from api.infrastructure.security.current_user import CurrentUser

logger = get_logger(__name__)

def check_feature_access(feature_name: FeatureEnum) -> callable:
    async def dependency(request: Request, currentUser: CurrentUser) -> bool:
        role: RoleDto = currentUser.role
        if not currentUser.tenant_id:
            if Permission.HOST_MANAGE_TENANTS in role.permissions:
                return True  # Host users with tenant management permissions have access to all features.
        
        if not currentUser.tenant_id:
            logger.info(f"User {currentUser.id} does not belong to any tenant and lacks host permissions.")
            raise FeatureNotEnabledException(feature_name.value)
        
        tenant_id = request.state.tenant_id
        if str(tenant_id) != str(currentUser.tenant_id):
            raise FeatureNotEnabledException(feature_name.value)

        tenant = await get_tenant_service().get_tenant_by_id(tenant_id=tenant_id)
        for entry in tenant.features or []:
            if isinstance(entry, dict):
                name, enabled = entry.get("name"), entry.get("enabled")
            else:
                name, enabled = entry.name, entry.enabled
            if name == feature_name:
                if enabled is True:
                    return True
                break
        raise FeatureNotEnabledException(feature_name.value)

    return dependency
