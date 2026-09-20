"""Validate Keycloak access tokens (RS256 + JWKS)."""

from __future__ import annotations

import asyncio
import logging
from functools import lru_cache

import jwt
from jwt import InvalidTokenError, PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError

from api.core.config import settings

logger = logging.getLogger(__name__)


class KeycloakJWTValidator:
    def __init__(
        self,
        jwks_url: str,
        issuer: str,
        client_id: str,
        jwks_request_host: str | None = None,
    ) -> None:
        self._jwks_url = jwks_url
        self._issuer = issuer
        self._client_id = client_id  # legacy single-id; validation uses settings.keycloak_allowed_azp_set()
        jwks_headers: dict[str, str] = {}
        if jwks_request_host:
            jwks_headers["Host"] = jwks_request_host.strip()
        self._jwks_client = PyJWKClient(jwks_url, headers=jwks_headers or None)

    def validate_sync(self, token: str) -> dict[str, object]:
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        except PyJWKClientConnectionError as e:
            logger.warning(
                "JWKS fetch failed for %s (Host header: %s): %s",
                self._jwks_url,
                self._jwks_client.headers.get("Host") if self._jwks_client.headers else None,
                e,
            )
            raise
        allowed_aud = settings.keycloak_allowed_audience_set()
        if not allowed_aud:
            raise InvalidTokenError("no allowed Keycloak audiences configured")
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            issuer=self._issuer,
            audience=list(allowed_aud),
            options={"verify_aud": True},
        )
        azp = payload.get("azp")
        allowed = settings.keycloak_allowed_azp_set()
        if azp not in allowed:
            raise InvalidTokenError(
                f"Invalid azp: {azp!r} not in allowed clients {sorted(allowed)!r}"
            )
        return payload

    async def validate(self, token: str) -> dict[str, object]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.validate_sync, token)


@lru_cache
def _validator_singleton() -> KeycloakJWTValidator:
    return KeycloakJWTValidator(
        jwks_url=settings.keycloak_jwks_url or "",
        issuer=settings.keycloak_issuer or "",
        client_id=settings.keycloak_client_id,
        jwks_request_host=settings.keycloak_jwks_request_host,
    )


def get_keycloak_jwt_validator() -> KeycloakJWTValidator:
    return _validator_singleton()
