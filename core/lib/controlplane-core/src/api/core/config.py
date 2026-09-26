from __future__ import annotations

from typing import Literal

from dotenv import load_dotenv
from pydantic import AliasChoices, EmailStr, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import BaseSettings

from controlplane_events.topics import CONTROLPLANE_EVENTS_V1

load_dotenv()

class Settings(BaseSettings):
    model_config = ConfigDict(
        env_file = ".env",
        env_file_encoding = "utf-8",
        extra="allow"
    )
    database_url: str = (
        "postgresql+asyncpg://controlplane:controlplane123@localhost:5432/controlplane"
    )

    jwt_secret: str = "dev-jwt-secret-change-me"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7 * 24 * 60 * 60

    algorithm: str = "HS256"
    refresh_algorithm: str = "HS512"
    refresh_token_secret: str = "dev-refresh-secret-change-me"

    admin_email: EmailStr = "chris@fyberlabs.com"
    admin_password: str = "Test@123!"

    app_title: str = "Full-Stack FastAPI React Template"
    app_version: str = "0.1.0"

    api_endpoint_base: str = "http://localhost:8000/api"

    multi_tenancy_strategy: str = "header"
    
    host_main_domain: str = "fsrapp.com"
    host_main_domain_prefix: str = "demo"  # Prefix for subdomains, e.g., demo.fsrapp.com. Change as needed.

    redis_uri: str = Field(
        default="redis://localhost:6372",
        validation_alias=AliasChoices("REDIS_URI", "REDIS_URL"),
    )
    celery_result_backend: str = "redis://localhost:6372/0"
    celery_broker_url: str = "redis://localhost:6372/0"


    fastapi_env: str = "development"  # Options: "development", "production"

    smtp_user: str = ""
    smtp_password: str = ""
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_mail_from_name: str = "FSR App"
    smtp_mail_from: EmailStr = "noreply@example.com"
    smtp_starttls: bool = False
    smtp_ssl_tls: bool = False
    smtp_use_credentials: bool = False # Changed to True when running production env
    smtp_validate_certs: bool = False

    ollama_host: str = "http://localhost:11434"

    
    # Coolify settings for deployment integration only used for domain setup
    coolify_enabled: bool = False
    coolify_api_url: str = ""
    coolify_api_key: str = ""
    coolify_application_id: str = ""


    # AWS S3 settings for file uploads this belongs to the Host - level settings
    aws_region: str = "eu-central-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_s3_bucket_name: str = "fsrapptest"

    # Stripe Settings for Billing and Payments - Host Level Settings
    stripe_api_key: str = ""
    stripe_publishable_key: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    # Kafka (optional leftover local bus; unset = no-op unless Service Bus is set)
    kafka_bootstrap_servers: str | None = Field(
        default=None,
        validation_alias="KAFKA_BOOTSTRAP_SERVERS",
    )
    kafka_events_topic: str = CONTROLPLANE_EVENTS_V1
    azure_service_bus_connection_string: str | None = Field(
        default=None,
        validation_alias="AZURE_SERVICE_BUS_CONNECTION_STRING",
        description=(
            "Production bus for controlplane.events.v1. When set, prefer Service Bus "
            "over leftover local Kafka. Do not put prompt bodies on this bus."
        ),
    )
    azure_service_bus_subscription: str | None = Field(
        default=None,
        validation_alias="AZURE_SERVICE_BUS_SUBSCRIPTION",
        description=(
            "Service Bus subscription on controlplane.events.v1. "
            "Unset uses the service consumer group name."
        ),
    )
    service_name: str = Field(
        default="controlplane",
        validation_alias="SERVICE_NAME",
        description="Identifies the process in event envelopes (gateway, user-service, …)",
    )

    @field_validator("kafka_bootstrap_servers", mode="before")
    @classmethod
    def _empty_kafka_none(cls, v: object) -> object:
        if v == "":
            return None
        return v

    @field_validator("azure_service_bus_connection_string", mode="before")
    @classmethod
    def _empty_service_bus_none(cls, v: object) -> object:
        if v == "":
            return None
        return v

    # Keycloak OIDC (required for API auth; compose sets these for Docker)
    keycloak_jwks_url: str | None = Field(
        default=None,
        validation_alias="KEYCLOAK_JWKS_URL",
        description="JWKS URL reachable from this process (Keycloak 26+: .../protocol/openid-connect/certs)",
    )
    keycloak_issuer: str | None = Field(
        default=None,
        validation_alias="KEYCLOAK_ISSUER",
        description="Must match access-token iss (e.g. http://localhost:8081/realms/controlplane)",
    )
    keycloak_client_id: str = Field(
        default="controlplane-frontend",
        validation_alias="KEYCLOAK_CLIENT_ID",
        description="Primary OIDC client id (legacy); see keycloak_allowed_client_ids for gateway validation.",
    )
    keycloak_allowed_client_ids: str = Field(
        default=(
            "controlplane-frontend,controlplane-mobile,controlplane-device,"
            "controlplane-m2m,controlplane-api-key,hypermesh-native"
        ),
        validation_alias="KEYCLOAK_ALLOWED_CLIENT_IDS",
        description=(
            "Comma-separated Keycloak client ids allowed in access-token azp (public + M2M). "
            "The default includes hypermesh-native. Set KEYCLOAK_ALLOWED_CLIENT_IDS without "
            "that id to revoke the visor/CLI client. Application code does not add it back."
        ),
    )
    keycloak_allowed_audiences: str = Field(
        default="account",
        validation_alias="KEYCLOAK_ALLOWED_AUDIENCES",
        description=(
            "Comma-separated JWT aud values accepted after signature verify. "
            "Client ids from KEYCLOAK_ALLOWED_CLIENT_IDS are always included. "
            "Default includes Keycloak's account resource."
        ),
    )
    # Optional Host header when fetching JWKS via an internal base URL (Keycloak hostname v2).
    keycloak_jwks_request_host: str | None = Field(
        default=None,
        validation_alias="KEYCLOAK_JWKS_REQUEST_HOST",
        description="Send as Host header on JWKS HTTP requests (e.g. localhost:8081 with docker-compose port map).",
    )

    # Single-tenant / dev: map OIDC users to ADMIN (beyond DB seed for ADMIN_EMAIL).
    bootstrap_admin_emails: str = Field(
        default="",
        validation_alias="BOOTSTRAP_ADMIN_EMAILS",
        description="Comma-separated extra emails → ADMIN on OIDC create/link (ADMIN_EMAIL is always included).",
    )
    entra_admin_group_ids: str = Field(
        default="",
        validation_alias="ENTRA_ADMIN_GROUP_IDS",
        description="Comma-separated Entra group object IDs; if access token 'groups' contains any, assign ADMIN.",
    )
    operator_oidc_domain: str = Field(
        default="",
        validation_alias="OPERATOR_OIDC_DOMAIN",
        description=(
            "Email domain and hosted domain (hd) for this instance's operator "
            "and owner population. Empty disables the map. Fyber example: "
            "fyberlabs.com. Another instance sets its own domain."
        ),
    )
    operator_oidc_group_map: str = Field(
        default="",
        validation_alias="OPERATOR_OIDC_GROUP_MAP",
        description=(
            "Inside OPERATOR_OIDC_DOMAIN, Keycloak groups mapped to claims. "
            "Entries separated by ';'. Each entry is group=claim|claim. "
            "Example: operator-support=operator:console|operator:support;"
            "operator-program=operator:console|operator:program|operator:resolve. "
            "Users outside the domain receive nothing from this map."
        ),
    )
    operator_oidc_user_map: str = Field(
        default="",
        validation_alias="OPERATOR_OIDC_USER_MAP",
        description=(
            "Inside OPERATOR_OIDC_DOMAIN, emails mapped to claims. Same grammar "
            "as OPERATOR_OIDC_GROUP_MAP. Owner is org:own, distinct from "
            "operator:support. Example: owner@fyberlabs.com=org:own."
        ),
    )
    oidc_relax_email_verified: bool = Field(
        default=False,
        validation_alias="OIDC_RELAX_EMAIL_VERIFIED",
        description="If True, do not reject access tokens with email_verified=false (common with some IdP brokers).",
    )
    oidc_extra_email_claims: str = Field(
        default="",
        validation_alias="OIDC_EXTRA_EMAIL_CLAIMS",
        description="Comma-separated extra JWT claim names to resolve email (after standard OIDC / Entra claims).",
    )

    # Auth-service OIDC (authorization code, refresh, optional introspection)
    keycloak_client_secret: str = Field(
        default="",
        validation_alias="KEYCLOAK_CLIENT_SECRET",
        description="Confidential client secret for token exchange / introspection from backend.",
    )
    auth_oidc_redirect_uri: str = Field(
        default="http://localhost:5173/auth/callback",
        validation_alias="AUTH_OIDC_REDIRECT_URI",
        description="Public SPA redirect URI registered in Keycloak for the auth code flow.",
    )
    auth_token_validation_mode: Literal["jwt", "introspection"] = Field(
        default="jwt",
        validation_alias="AUTH_TOKEN_VALIDATION_MODE",
    )
    auth_tenant_claim_names: str = Field(
        default="tenant_id,https://panopticon.dev/tenant_id",
        validation_alias="AUTH_TENANT_CLAIM_NAMES",
        description="Comma-separated JWT claim names; first present value wins for tenant UUID.",
    )
    oidc_require_mfa: bool = Field(
        default=False,
        validation_alias="OIDC_REQUIRE_MFA",
        description="If True, /auth/me rejects tokens unless MFA is indicated (amr/acr).",
    )
    api_key_exchange_jwt_secret: str = Field(
        default="",
        validation_alias="API_KEY_EXCHANGE_JWT_SECRET",
        description=(
            "HS256 secret for short-lived JWTs minted after API key validation "
            "(gateway + auth validate). Empty or a published default fails closed."
        ),
    )
    api_keys_internal_token: str = Field(
        default="",
        validation_alias="API_KEYS_INTERNAL_TOKEN",
        description=(
            "Shared secret for gateway → api-keys-service internal validate. "
            "Empty or a published default fails closed."
        ),
    )
    payment_internal_token: str = Field(
        default="",
        validation_alias="PAYMENT_INTERNAL_TOKEN",
        description=(
            "P0-D internal settle credential for payment-service undelivered / "
            "refund hops. Not a browser JWT. Empty fails closed."
        ),
    )
    hypermesh_internal_token: str = Field(
        default="",
        validation_alias="HYPERMESH_INTERNAL_TOKEN",
        description=(
            "P0-D internal settle credential for Hypermesh "
            "POST /internal/payment-succeeded and owner-or-internal complete/payout. "
            "Not a browser JWT. Production consumes payment.succeeded.v1 from the bus."
        ),
    )

    acl_service_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ACL_SERVICE_URL", "acl_service_base_url"),
        description="Base URL for acl-service (POST /api/v1/acl/enforce). Empty fails closed (deny).",
    )
    payment_service_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("PAYMENT_SERVICE_URL", "payment_service_base_url"),
        description="Base URL for payment-service (optional; paid listing flow).",
    )

    ai_upstream_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AI_UPSTREAM_BASE_URL", "ai_upstream_base_url"),
        description="OpenAI-compatible base (e.g. https://api.openai.com/v1 or http://ollama:11434/v1).",
    )
    ai_upstream_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("AI_UPSTREAM_API_KEY", "OPENAI_API_KEY"),
        description="Bearer token for upstream LLM (optional for local Ollama).",
    )
    ai_default_max_tokens: int = Field(
        default=256,
        validation_alias="AI_DEFAULT_MAX_TOKENS",
        ge=1,
        le=128_000,
    )
    ai_cost_per_1k_input_usd: float = Field(
        default=0.002,
        validation_alias="AI_COST_PER_1K_INPUT_USD",
        ge=0.0,
    )
    ai_cost_per_1k_output_usd: float = Field(
        default=0.006,
        validation_alias="AI_COST_PER_1K_OUTPUT_USD",
        ge=0.0,
    )
    ai_lamports_per_1k_tokens: int = Field(
        default=1_000,
        validation_alias="AI_LAMPORTS_PER_1K_TOKENS",
        ge=1,
        description="Crypto budget: lamports charged per 1000 tokens (estimated + actual settle).",
    )
    ai_rate_limit_per_minute: int = Field(
        default=120,
        validation_alias="AI_RATE_LIMIT_PER_MINUTE",
        ge=1,
        description="Per-tenant per-user sliding window (in-process; reset on restart).",
    )
    acl_rate_limit_per_minute: int = Field(
        default=240,
        validation_alias="ACL_RATE_LIMIT_PER_MINUTE",
        ge=1,
        description="Rate limit for POST /api/v1/acl/enforce (tenant+user sliding window).",
    )
    ai_stripe_meter_event_name: str = Field(
        default="ai_inference_tokens",
        validation_alias="AI_STRIPE_METER_EVENT_NAME",
        min_length=1,
        max_length=128,
    )

    def oidc_bootstrap_admin_email_set(self) -> frozenset[str]:
        emails = {str(self.admin_email).strip().lower()}
        for part in self.bootstrap_admin_emails.split(","):
            s = part.strip().lower()
            if s:
                emails.add(s)
        return frozenset(emails)

    def oidc_entra_admin_group_id_set(self) -> frozenset[str]:
        return frozenset(
            x.strip().lower()
            for x in self.entra_admin_group_ids.split(",")
            if x.strip()
        )

    def oidc_extra_email_claim_keys(self) -> tuple[str, ...]:
        return tuple(
            x.strip()
            for x in self.oidc_extra_email_claims.split(",")
            if x.strip()
        )

    def auth_tenant_claim_name_list(self) -> tuple[str, ...]:
        return tuple(
            x.strip()
            for x in self.auth_tenant_claim_names.split(",")
            if x.strip()
        )

    def keycloak_allowed_azp_set(self) -> frozenset[str]:
        ids = {x.strip() for x in self.keycloak_allowed_client_ids.split(",") if x.strip()}
        if self.keycloak_client_id:
            ids.add(self.keycloak_client_id.strip())
        return frozenset(ids)

    def keycloak_allowed_audience_set(self) -> frozenset[str]:
        ids = {x.strip() for x in self.keycloak_allowed_audiences.split(",") if x.strip()}
        ids.update(self.keycloak_allowed_azp_set())
        return frozenset(ids)

    def usable_api_key_exchange_secret(self) -> str | None:
        return _usable_shared_secret(self.api_key_exchange_jwt_secret, _PUBLISHED_API_KEY_SECRETS)

    def usable_api_keys_internal_token(self) -> str | None:
        return _usable_shared_secret(self.api_keys_internal_token, _PUBLISHED_API_KEY_SECRETS)

    @field_validator(
        "keycloak_jwks_url",
        "keycloak_issuer",
        "keycloak_jwks_request_host",
        "acl_service_base_url",
        "payment_service_base_url",
        "ai_upstream_base_url",
        mode="before",
    )
    @classmethod
    def _empty_keycloak_none(cls, v: object) -> object:
        if v == "":
            return None
        return v

    @model_validator(mode="after")
    def _keycloak_local_defaults(self) -> Settings:
        if self.keycloak_jwks_url is None:
            self.keycloak_jwks_url = (
                "http://localhost:8081/realms/controlplane/protocol/openid-connect/certs"
            )
        if self.keycloak_issuer is None:
            self.keycloak_issuer = "http://localhost:8081/realms/controlplane"
        return self


# Published compose/docs defaults. A leak of these is an any-tenant forgery.
_PUBLISHED_API_KEY_SECRETS = frozenset(
    {
        "dev-api-keys-internal-token",
        "dev-api-key-exchange-secret-change-in-production-min-32-chars!!",
    }
)


def _usable_shared_secret(raw: str | None, banned: frozenset[str]) -> str | None:
    value = (raw or "").strip()
    if not value or value in banned:
        return None
    return value


# Instantiate settings once
settings = Settings()

