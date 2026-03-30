from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, List, Literal, Optional
from urllib.parse import urlparse

from pydantic import AfterValidator, BaseModel
from controlplane_db.models import Tenant

from api.common.utils import get_host_main_domain_name
from api.core.exceptions import InvalidCustomDomainException, InvalidSubdomainException
from api.domain.enum.feature import Feature as FeatureEnum

SUBDOMAIN_REGEX = re.compile(r"^(?!-)[A-Za-z0-9-]{3,63}(?<!-)$")
CUSTOM_DOMAIN_REGEX = re.compile(r"^(?:[a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}$")


def validate_subdomain(subdomain: str | None) -> str | None:
    if subdomain is None:
        return None
    main_domain = get_host_main_domain_name()
    if (not subdomain.endswith(f".{main_domain}") and subdomain != main_domain) or subdomain == main_domain:
        raise InvalidSubdomainException(subdomain)
    label = subdomain.removesuffix(f".{main_domain}")
    if not SUBDOMAIN_REGEX.match(label):
        raise InvalidSubdomainException(subdomain)
    return subdomain


def validate_custom_domain(domain: str) -> str:
    if not domain:
        raise InvalidCustomDomainException("Custom domain is required.")
    parsed = urlparse(domain if "://" in domain else f"http://{domain}")
    hostname = parsed.hostname
    if not hostname:
        raise InvalidCustomDomainException("Invalid domain format.")
    if parsed.path not in ("", "/") or parsed.query:
        raise InvalidCustomDomainException("Custom domain must not include paths or query parameters.")
    if not CUSTOM_DOMAIN_REGEX.match(hostname):
        raise InvalidCustomDomainException("Invalid domain format.")
    main_domain = get_host_main_domain_name()
    if hostname == main_domain or hostname.endswith(f".{main_domain}"):
        raise InvalidCustomDomainException(
            "Custom domain cannot be your application's main domain or its subdomain."
        )
    return hostname


Subdomain = Annotated[str, AfterValidator(validate_subdomain)]
CustomDomain = Annotated[str, AfterValidator(validate_custom_domain)]


class Feature(BaseModel):
    name: FeatureEnum
    enabled: bool


__all__ = [
    "Tenant",
    "Feature",
    "validate_subdomain",
    "validate_custom_domain",
    "Subdomain",
    "CustomDomain",
]
