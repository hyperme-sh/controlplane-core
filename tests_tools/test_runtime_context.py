"""Reject unqualified/direct-source runtime dependencies before building an image."""
import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "runtime_context", Path(__file__).resolve().parents[1] / "scripts/prepare-runtime-context.py"
)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


@pytest.mark.parametrize("export", [
    "unknown-owner @ git+https://github.com/untrusted/runtime.git@" + "a" * 40,
    "httpx==0.28.1",
])
def test_unverified_runtime_git_graph_is_refused(tmp_path, monkeypatch, export):
    distribution = tmp_path / "dist"
    distribution.mkdir()
    (distribution / "controlplane_core-0.1.0-py3-none-any.whl").write_bytes(b"unit-test-only")
    monkeypatch.setattr(runtime.fixtures, "verify", lambda root: [])
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: None)
    monkeypatch.setattr(runtime.subprocess, "check_output", lambda *args, **kwargs: export)
    with pytest.raises(ValueError, match="verified exact-source wheel"):
        runtime.prepare(distribution, tmp_path / "context")
    assert not (tmp_path / "context").exists()


def test_direct_public_source_cannot_enter_runtime_lock(tmp_path, monkeypatch):
    distribution = tmp_path / "dist"
    distribution.mkdir()
    (distribution / "controlplane_core-0.1.0-py3-none-any.whl").write_bytes(b"unit-test-only")
    monkeypatch.setattr(runtime.fixtures, "verify", lambda root: [])
    monkeypatch.setattr(runtime.subprocess, "run", lambda *args, **kwargs: None)
    responses = iter([
        "controlplane-api @ git+https://github.com/hyperme-sh/controlplane-api.git@" + "a" * 40,
        "unexpected @ https://example.invalid/source.tar.gz",
    ])
    monkeypatch.setattr(runtime.subprocess, "check_output", lambda *args, **kwargs: next(responses))
    with pytest.raises(ValueError, match="direct source references"):
        runtime.prepare(distribution, tmp_path / "context")
    assert not (tmp_path / "context").exists()
