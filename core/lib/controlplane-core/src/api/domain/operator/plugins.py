"""Static in-repo operator plugins. No remote manifests. No Payments plugin."""

from __future__ import annotations

from typing import Any

TENNESSEE_EMPTY_REASON = (
    "Tennessee Windage missions, devices, and reticle are not an operator "
    "dashboard in this cut. The slot stays empty until that module is written "
    "in this repo."
)

PLUGINS: list[dict[str, Any]] = [
    {
        "id": "hypermesh.aiuditor",
        "network": "hypermesh",
        "title": "AIuditor",
        "kind": "builtin",
        "reads": [
            "/api/v1/aiuditor/meta",
            "/api/v1/aiuditor/connections",
            "/api/v1/aiuditor/violations",
            "/api/v1/aiuditor/suite",
        ],
        "actions": ["violation_create", "approval_resolve", "suite_edit"],
        "permission": "operator:support",
        "program_actions": ["suite_edit"],
    },
    {
        "id": "hypermesh.models",
        "network": "hypermesh",
        "title": "Models",
        "kind": "builtin",
        "reads": [
            "app_configuration.model_name",
            "/api/v1/hypermesh/catalog",
            "/api/v1/aiuditor/suite",
            "/api/v1/aiuditor/envelopes",
        ],
        "actions": ["suite_edit"],
        "permission": "operator:support",
        "program_actions": ["suite_edit"],
    },
    {
        "id": "hypermesh.hosts",
        "network": "hypermesh",
        "title": "Hosts",
        "kind": "builtin",
        "reads": ["/api/v1/hypermesh/devices"],
        "actions": [
            "sell_pause",
            "schedule_hold",
            "schedule_restore",
            "deenroll",
            "enroll_token",
        ],
        "permission": "operator:support",
        "resolve_actions": ["schedule_hold", "schedule_restore"],
    },
    {
        "id": "hypermesh.aimmune",
        "network": "hypermesh",
        "title": "AImmune",
        "kind": "builtin",
        "reads": [
            "/api/v1/auditor/v0/tickets/{id}",
            "/api/v1/grants/v0/grants/{id}",
        ],
        "actions": ["ticket_resolve", "grant_resolve", "grant_revoke"],
        "permission": "operator:resolve",
    },
    {
        "id": "tennessee-windage",
        "network": "tennessee-windage",
        "title": "Tennessee Windage",
        "kind": "slot",
        "reads": [],
        "actions": [],
        "permission": "operator:console",
        "empty_reason": TENNESSEE_EMPTY_REASON,
    },
]


def plugin_registry() -> list[dict[str, Any]]:
    return [dict(row) for row in PLUGINS]


def plugin_by_id(plugin_id: str) -> dict[str, Any] | None:
    for row in PLUGINS:
        if row["id"] == plugin_id:
            return dict(row)
    return None


def registry_has_payments() -> bool:
    for row in PLUGINS:
        if "payment" in row["id"] or row["title"].lower() == "payments":
            return True
    return False


def known_action(plugin_id: str, action: str) -> bool:
    row = plugin_by_id(plugin_id)
    if row is None or row["kind"] != "builtin":
        return False
    return action in set(row.get("actions") or [])
