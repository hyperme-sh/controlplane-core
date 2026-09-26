"""Unauthenticated build identity for every service process.

The payload is only the fields callers need to see which image is running.
It reads the git sha and build time baked into the image. It does not read
the deploy tag, tokens, database URLs, or product settings.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter()

_GIT_SHA = re.compile(r"^[0-9a-f]{40}$")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
_STAMP = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")
_SERVICE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def _env(source: Mapping[str, str], key: str) -> str:
    return str(source.get(key, "")).strip()


def _git_sha(value: str) -> str:
    text = value.strip().lower()
    if _GIT_SHA.fullmatch(text):
        return text
    return ""


def _digest(value: str) -> str:
    text = value.strip().lower()
    match = _DIGEST.search(text)
    if match is None:
        return ""
    return match.group(0)


def version_payload(env: Mapping[str, str] | None = None) -> dict[str, str]:
    """Build identity for this process.

    ``GIT_SHA`` is the commit that built this image. A carried image keeps
    that value; retagging it onto a new deploy sha does not change it.
    ``SOURCE_SHA`` and ``HYPERMESH_SOURCE_SHA`` cover images that only baked
    the hypermesh source sha. Compose does not inject the deploy sha: that
    value in every service's environment would change the rendered config
    hash and recreate the plane on a docs-only move.
    """

    source = os.environ if env is None else env
    git_sha = (
        _git_sha(_env(source, "GIT_SHA"))
        or _git_sha(_env(source, "SOURCE_SHA"))
        or _git_sha(_env(source, "HYPERMESH_SOURCE_SHA"))
        or "unknown"
    )

    service = _env(source, "SERVICE_NAME").lower()
    if not _SERVICE.fullmatch(service):
        service = "unknown"

    built_at = _env(source, "BUILT_AT")
    if not _STAMP.fullmatch(built_at):
        built_at = "unknown"

    body = {
        "service": service,
        "git_sha": git_sha,
        "built_at": built_at,
    }
    digest = _digest(_env(source, "PANOPTICON_IMAGE_DIGEST")) or _digest(
        _env(source, "IMAGE_DIGEST")
    )
    if digest:
        body["image_digest"] = digest
    return body


@router.get("/version")
async def version() -> JSONResponse:
    return JSONResponse(version_payload())
