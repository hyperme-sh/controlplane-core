from collections import deque
import json
from pathlib import Path
from controlplane_api import audit_files
from typing import List, Optional
from controlplane_api.utils import capture_audit_log, get_logger
from controlplane_api.dtos.audit import AuditActionType, AuditLogDto, AuditLogListDto

logger = get_logger(__name__)

class AuditLogRepository:

    async def _get_files_matching_pattern(self, tenant_id: str | None = None) -> List[str]:
        return [str(path) for path in audit_files.log_paths(tenant_id)]

    async def add_audit_log(self, audit_log: AuditLogDto) -> None:
        await capture_audit_log(audit_log)

    async def list_audit_logs(self, tenant_id: str | None = None, limit: int = 10, skip: int = 0, action: Optional[AuditActionType] = None) -> AuditLogListDto:
       
        files = await self._get_files_matching_pattern(tenant_id=tenant_id)

        total = 0
        keep = skip + limit
        ring = deque(maxlen=keep) # keep the last (skip + limit) lines

        for file_path in files:
            try:
                with audit_files.open_file(Path(file_path).name) as f:
                    for line in f:
                        obj = json.loads(line)
                        if action and obj.get("action") != action:
                            continue
                        total += 1
                        ring.append(obj)  # older ones drop automatically when full
            except FileNotFoundError:
                continue

        # Now ring holds the last (skip+limit) lines globally (latest lines overall)
        # Convert to newest-first
        newest_first = list(ring)[::-1]

        # Slice for pagination
        sliced = newest_first[skip:skip + limit]

        # Parse JSON and map to DTOs
        logs: List[AuditLogDto] = [AuditLogDto(**o) for o in sliced]

        return AuditLogListDto(
            total=total,
            logs=logs,
            has_next=(skip + limit) < total,
            has_previous=skip > 0,
            limit=limit,
            skip=skip
        )

    async def return_all_audit_logs(self, action: Optional[AuditActionType] = None, tenant_id: str | None = None) -> List[AuditLogDto]:
        files = await self._get_files_matching_pattern(tenant_id=tenant_id)
        results: List[AuditLogDto] = []
        for file_path in files:
            try:
                with audit_files.open_file(Path(file_path).name) as f:
                    for line in f:
                        obj = json.loads(line)
                        if action and obj.get("action") != action:
                            continue
                        results.append(AuditLogDto(**obj))
            except FileNotFoundError:
                continue
    
        if len(results) == 0:
            logger.info("No audit logs found.")
            return []
    
        return results[::-1]  # return newest first