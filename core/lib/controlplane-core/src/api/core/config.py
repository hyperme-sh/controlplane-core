from __future__ import annotations

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

    # Kafka (optional; unset = no-op publisher)
    kafka_bootstrap_servers: str | None = Field(
        default=None,
        validation_alias="KAFKA_BOOTSTRAP_SERVERS",
    )
    kafka_events_topic: str = CONTROLPLANE_EVENTS_V1
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

    # Keycloak OIDC (required for API auth; compose sets these for Docker)
    keycloak_jwks_url: str | None = Field(
        default=None,
        validation_alias="KEYCLOAK_JWKS_URL",
        description="JWKS URL reachable from this process (e.g. http://keycloak:8080/.../openid-certs)",
    )
    keycloak_issuer: str | None = Field(
        default=None,
        validation_alias="KEYCLOAK_ISSUER",
        description="Must match access-token iss (e.g. http://localhost:8081/realms/controlplane)",
    )
    keycloak_client_id: str = Field(
        default="controlplane-frontend",
        validation_alias="KEYCLOAK_CLIENT_ID",
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

    @field_validator("keycloak_jwks_url", "keycloak_issuer", mode="before")
    @classmethod
    def _empty_keycloak_none(cls, v: object) -> object:
        if v == "":
            return None
        return v

    @model_validator(mode="after")
    def _keycloak_local_defaults(self) -> Settings:
        if self.keycloak_jwks_url is None:
            self.keycloak_jwks_url = (
                "http://localhost:8081/realms/controlplane/protocol/openid-certs"
            )
        if self.keycloak_issuer is None:
            self.keycloak_issuer = "http://localhost:8081/realms/controlplane"
        return self


# Instantiate settings once
settings = Settings()

