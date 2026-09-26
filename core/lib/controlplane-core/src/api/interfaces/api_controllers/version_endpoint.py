"""Unauthenticated build identity for every service process.

The payload is only the four fields callers need to see which image is
running. It reads build args baked by ghcr-test.yml and the deploy pointer
compose injects. It does not read tokens, database URLs, or product settings.
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

    ``PANOPTICON_IMAGE_TAG`` is the sha ``deploy/test`` checked out. Carried
    images keep the ``GIT_SHA`` of the commit that last built them, so a
    full git sha in the deploy pointer wins. A mutable tag such as ``test``
    does not hide the baked sha.
    """

    source = os.environ if env is None else env
    baked = (
        _git_sha(_env(source, "GIT_SHA"))
        or _git_sha(_env(source, "SOURCE_SHA"))
        or _git_sha(_env(source, "HYPERMESH_SOURCE_SHA"))
    )
    deployed = _git_sha(_env(source, "PANOPTICON_IMAGE_TAG"))
    git_sha = deployed or baked or "unknown"

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
