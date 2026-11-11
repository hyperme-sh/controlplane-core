"""Explicit storage owner scope; platform/global rows are never a tenant default."""
from uuid import UUID


def storage_tenant(value: UUID | str | None) -> UUID:
    try:
        tenant = UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("A tenant storage scope is required") from exc
    if tenant.int == 0:
        raise ValueError("A tenant storage scope is required")
    return tenant
