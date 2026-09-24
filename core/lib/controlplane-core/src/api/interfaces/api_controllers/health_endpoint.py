import asyncio
import os
import urllib.error
import urllib.request

from fastapi import APIRouter, status
from fastapi.responses import JSONResponse

from api.common.dtos.health_dto import HealthCheckResponseDto
from api.common.utils import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/health")
router.tags = ["Health"]

_CATALOG_PROBE_TIMEOUT_S = 2.0


def hypermesh_catalog_reachable(base_url: str, timeout: float = _CATALOG_PROBE_TIMEOUT_S) -> bool:
    """True when hypermesh-service answers catalog. Connection failure is false."""

    url = base_url.rstrip("/") + "/api/v1/hypermesh/catalog"
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status == 200
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


@router.get(
    "/",
    response_model=HealthCheckResponseDto,
    responses={status.HTTP_503_SERVICE_UNAVAILABLE: {"model": HealthCheckResponseDto}},
)
async def health_check():
    # Only the edge sets this. Other services keep the plain Ok check.
    # HAProxy on the test plane expects 200 from this route, so a down
    # hypermesh-service has to fail here or the public health stays green.
    base = os.environ.get("CPEDGE_HYPERMESH_SERVICE_URL", "").strip()
    if base and not await asyncio.to_thread(hypermesh_catalog_reachable, base):
        logger.warning("hypermesh catalog probe failed")
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "hypermesh unreachable"},
        )
    return HealthCheckResponseDto(status="Ok")
