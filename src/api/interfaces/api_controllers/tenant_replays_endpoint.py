"""Consume-only tenant replay API; no read, reset or delete operation."""
import re

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, Response

from api.domain.tenant_objects import ObjectInputError, ObjectUnavailable
from api.domain.tenant_replays import ReplayUsed, TenantReplays
from api.interfaces.api_controllers.tenant_records_endpoint import records_store

router = APIRouter(prefix="/storage/replays", tags=["Tenant replays"])
capacity = anyio.CapacityLimiter(4)


def replay_store(records=Depends(records_store)):
    return TenantReplays(records.tenant, records.backend)


@router.post("/{namespace}/{nonce}")
async def consume_replay(namespace: str, nonce: str, request: Request, store=Depends(replay_store)):
    # A single canonical integer header avoids an unbounded request body/parser.
    expiry = request.headers.getlist("x-replay-expires-at")
    lengths = request.headers.getlist("content-length")
    if (len(expiry) != 1 or not re.fullmatch(r"[1-9][0-9]{0,15}", expiry[0])
            or lengths != ["0"] or request.headers.getlist("transfer-encoding")
            or request.headers.getlist("content-encoding")):
        raise HTTPException(400, "Bounded replay request required")
    try:
        with anyio.fail_after(10):
            async with capacity:
                async for chunk in request.stream():
                    if chunk:
                        raise ObjectInputError("Empty replay body required")
                await store.consume(namespace, nonce, int(expiry[0]))
        return Response(status_code=204, headers={"Cache-Control": "private, no-store"})
    except ReplayUsed:
        raise HTTPException(409, "Replay already consumed") from None
    except ObjectInputError:
        raise HTTPException(400, "Invalid replay request") from None
    except TimeoutError:
        raise HTTPException(504, "Replay operation timed out") from None
    except ObjectUnavailable:
        raise HTTPException(503, "Replay storage unavailable") from None
