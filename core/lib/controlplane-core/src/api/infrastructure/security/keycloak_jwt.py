"""Compatibility alias for controlplane_identity.security.keycloak_jwt."""
import sys
from api.core.config import settings as _configured_settings
from controlplane_identity.security import keycloak_jwt as _implementation
from controlplane_identity.security.keycloak_jwt import logger, KeycloakJWTValidator, get_keycloak_jwt_validator

__all__ = ['logger', 'KeycloakJWTValidator', 'get_keycloak_jwt_validator']

sys.modules[__name__] = _implementation
