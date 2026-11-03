import importlib

def test_legacy_imports_share_implementation_and_session_context():
    mappings = {
        "api.common.internal_token": "controlplane_identity.internal_token",
        "api.common.person_bind": "controlplane_identity.person_bind",
        "api.common.portal_controls": "controlplane_identity.portal_controls",
        "api.infrastructure.externals.api_keys_client": "controlplane_identity.api_keys_client",
        "api.infrastructure.security.keycloak_jwt": "controlplane_identity.security.keycloak_jwt",
        "api.infrastructure.security.api_key_exchange_jwt": "controlplane_identity.security.api_key_exchange_jwt",
        "api.infrastructure.security.tenant_session_jwt": "controlplane_identity.security.tenant_session_jwt",
        "api.infrastructure.persistence.db_session": "controlplane_tenancy.db_session",
        "api.infrastructure.persistence.tenant_guc": "controlplane_tenancy.tenant_guc",
        "api.infrastructure.persistence.tenant_membership": "controlplane_tenancy.tenant_membership",
    }
    for old, new in mappings.items():
        assert importlib.import_module(old) is importlib.import_module(new)
