"""Map SQLAlchemy ORM rows to dicts / API-friendly shapes (replaces Beanie to_serializable_dict)."""

from __future__ import annotations

import uuid
from typing import Any

from controlplane_db import models as m


def _base(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "tenant_id": str(row.tenant_id) if row.tenant_id else None,
    }


def serialize_user(row: m.User) -> dict[str, Any]:
    d = _base(row)
    d.update(
        {
            "first_name": row.first_name,
            "last_name": row.last_name,
            "email": row.email,
            "gender": row.gender,
            "role_id": str(row.role_id) if row.role_id else None,
            "is_active": row.is_active,
            "activated_at": row.activated_at.isoformat() if row.activated_at else None,
            "image_url": row.image_url,
            "sso_provider_id": row.sso_provider_id,
            "keycloak_sub": row.keycloak_sub,
        }
    )
    return d


def serialize_role(row: m.Role) -> dict[str, Any]:
    d = _base(row)
    perms = row.permissions or []
    d.update(
        {
            "name": row.name,
            "description": row.description,
            "permissions": [p if isinstance(p, str) else getattr(p, "value", str(p)) for p in perms],
        }
    )
    return d


def serialize_tenant(row: m.Tenant) -> dict[str, Any]:
    feats = row.features or []
    return {
        "id": str(row.id),
        "name": row.name,
        "subdomain": row.subdomain,
        "created_at": row.created_at.isoformat() if getattr(row, "created_at", None) else None,
        "updated_at": row.updated_at.isoformat() if getattr(row, "updated_at", None) else None,
        "is_active": row.is_active,
        "custom_domain": row.custom_domain,
        "custom_domain_status": row.custom_domain_status,
        "features": list(feats),
        "subscription_id": str(row.subscription_id) if row.subscription_id else None,
    }


def serialize_user_password_reset(row: m.UserPasswordReset) -> dict[str, Any]:
    d = _base(row)
    d.update(
        {
            "user_id": str(row.user_id),
            "token_secret": row.token_secret,
            "reset_secret_updated_at": row.reset_secret_updated_at.isoformat(),
            "first_name": row.first_name,
        }
    )
    return d


def serialize_user_preference(row: m.UserPreference) -> dict[str, Any]:
    d = _base(row)
    d.update({"user_id": str(row.user_id), "preferences": row.preferences or {}})
    return d


def serialize_storage_settings(row: m.StorageSettings) -> dict[str, Any]:
    d = _base(row)
    d.update(
        {
            "provider": row.provider,
            "is_enabled": row.is_enabled,
            "region": row.region,
            "aws_access_key": row.aws_access_key,
            "aws_secret_key": row.aws_secret_key,
            "aws_bucket_name": row.aws_bucket_name,
            "azure_connection_string": row.azure_connection_string,
            "azure_container_name": row.azure_container_name,
            "updated_by_user_id": row.updated_by_user_id,
        }
    )
    return d


def serialize_chat_history(row: m.ChatHistoryAI) -> dict[str, Any]:
    d = _base(row)
    d["histories"] = list(row.histories or [])
    return d


def serialize_chat_session(row: m.ChatSessionAI) -> dict[str, Any]:
    d = _base(row)
    d.update(
        {
            "session_id": str(row.session_id),
            "user_id": str(row.user_id),
            "history_id": str(row.history_id),
        }
    )
    return d


def parse_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    return uuid.UUID(str(value))
