"""Compare internal settle tokens. Not a browser JWT door."""

from __future__ import annotations

import hmac

from fastapi import Request


INTERNAL_TOKEN_HEADER = "X-Internal-Token"


def provided_internal_token(request: Request) -> str | None:
    raw = request.headers.get(INTERNAL_TOKEN_HEADER)
    if raw is None or not str(raw).strip():
        return None
    return str(raw).strip()


def internal_token_matches(provided: str | None, expected: str | None) -> bool:
    if not provided or not expected:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8"))


def request_has_internal_token(request: Request, expected: str | None) -> bool:
    return internal_token_matches(provided_internal_token(request), expected)
