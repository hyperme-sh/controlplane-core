"""Compatibility alias for API scaffolding."""
import sys
import controlplane_api.dtos.audit as _implementation
from controlplane_api.dtos.audit import AuditActionType, AuditLogDto, AuditLogListDto, DownloadAuditRequestDto
__all__ = ['AuditActionType', 'AuditLogDto', 'AuditLogListDto', 'DownloadAuditRequestDto']
sys.modules[__name__] = _implementation
