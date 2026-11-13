"""Private worker transaction and credential-file contracts."""
import os
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from api import experiment_guardrails as worker


def test_query_credential_file_must_be_private_regular_and_owned(tmp_path):
    path = tmp_path / "secret"
    path.write_text("a" * 32)
    path.chmod(0o600)
    assert worker.private_secret(str(path)) == "a" * 32
    path.chmod(0o644)
    with pytest.raises(ValueError): worker.private_secret(str(path))
    path.chmod(0o600)
    link = tmp_path / "link"
    link.symlink_to(path)
    with pytest.raises(OSError): worker.private_secret(str(link))
    os.link(path, tmp_path / "hardlink")
    with pytest.raises(ValueError): worker.private_secret(str(path))
    with pytest.raises(ValueError): worker.private_secret("relative")


@pytest.mark.asyncio
@pytest.mark.parametrize("failed", [False, True])
async def test_controller_worker_preserves_tenant_transaction_and_cleans_up(monkeypatch, failed):
    tenant = UUID("12345678-1234-4234-8234-123456789abc")
    session = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())
    @asynccontextmanager
    async def maker(): yield session
    @asynccontextmanager
    async def provider(selected):
        assert selected == tenant
        yield "objects"
    guc, reset = AsyncMock(), Mock()
    disable = AsyncMock(return_value=("signup",))
    if failed: disable.side_effect = ValueError("query unavailable")
    monkeypatch.setattr(worker, "set_db_session", Mock(return_value="context"))
    monkeypatch.setattr(worker, "reset_db_session", reset)
    monkeypatch.setattr(worker, "set_current_tenant_id_guc", guc)
    monkeypatch.setattr(worker, "tenant_backend", provider)
    monkeypatch.setattr(worker, "listing", lambda _: ("records", "page"))
    monkeypatch.setattr(worker, "disable_regressed", disable)
    if failed:
        with pytest.raises(ValueError): await worker.tick(maker, tenant, "hyperme.sh", "metrics")
        session.rollback.assert_awaited_once(); session.commit.assert_not_awaited()
    else:
        assert await worker.tick(maker, tenant, "hyperme.sh", "metrics") == ("signup",)
        session.commit.assert_awaited_once(); session.rollback.assert_not_awaited()
    assert disable.await_args.kwargs == {"tenant": tenant, "audience": "hyperme.sh"}
    assert disable.await_args.args[1] == "metrics"
    guc.assert_awaited_once_with(session, tenant)
    reset.assert_called_once_with("context")
