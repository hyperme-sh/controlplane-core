"""Audit writer/reader contract using private files; no service or database."""
import json
import pytest
from controlplane_api import audit_files
from controlplane_api.dtos.audit import AuditLogDto
from api.common.audit_logs_repository import AuditLogRepository


@pytest.fixture(autouse=True)
def private_tmp(tmp_path, monkeypatch):
    monkeypatch.setattr(audit_files.tempfile, 'gettempdir', lambda: str(tmp_path))


@pytest.mark.asyncio
async def test_reader_and_writer_share_private_tenant_storage():
    repository = AuditLogRepository()
    for tenant, entity in [('one','first'), ('two','foreign'), ('one','latest')]:
        await repository.add_audit_log(AuditLogDto(tenant_id=tenant, entity=entity, action='read'))
    page = await repository.list_audit_logs('one', limit=1)
    assert page.total == 2
    assert [log.entity for log in page.logs] == ['latest']
    assert page.has_next
    assert [log.entity for log in await repository.return_all_audit_logs(tenant_id='one')] == ['latest','first']
    assert (await repository.list_audit_logs(None)).total == 0


@pytest.mark.asyncio
async def test_reader_rejects_globs_and_symlinked_logs(tmp_path):
    repository = AuditLogRepository()
    with pytest.raises(ValueError):
        await repository.list_audit_logs('*')
    target = tmp_path/'private-data'
    target.write_text(json.dumps({'entity':'foreign','action':'read'}))
    (audit_files.directory()/'audit_logs_forone_20261112.jsonl').symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        await repository.return_all_audit_logs(tenant_id='one')
