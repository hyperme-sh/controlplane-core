"""Compatibility alias for API scaffolding."""
import sys
import controlplane_api.utils as _implementation
from controlplane_api.utils import get_utc_now, get_logger, is_tenancy_enabled, get_tenancy_strategy, get_host_main_domain_name, get_app_environment, get_app_title, is_production_environment, is_development_environment, PASSWORD_REGEX, validate_password, get_email_sharing_link, get_date_range, format_bytes_to_human_readable_size, is_subdomain, create_temp_file, capture_audit_log, get_sso_redirect_uri
__all__ = ['get_utc_now', 'get_logger', 'is_tenancy_enabled', 'get_tenancy_strategy', 'get_host_main_domain_name', 'get_app_environment', 'get_app_title', 'is_production_environment', 'is_development_environment', 'PASSWORD_REGEX', 'validate_password', 'get_email_sharing_link', 'get_date_range', 'format_bytes_to_human_readable_size', 'is_subdomain', 'create_temp_file', 'capture_audit_log', 'get_sso_redirect_uri']
sys.modules[__name__] = _implementation
