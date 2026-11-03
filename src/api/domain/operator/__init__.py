"""Operator console domain: claims, bans, mail, peers, plugins."""

from api.domain.operator.bans import ban_book
from api.domain.operator.claims import (
    console_allowed,
    permissions_from_claims,
    program_may_edit_suite,
    support_allowed,
)

__all__ = [
    "ban_book",
    "console_allowed",
    "permissions_from_claims",
    "program_may_edit_suite",
    "support_allowed",
]
