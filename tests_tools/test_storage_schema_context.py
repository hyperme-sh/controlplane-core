"""Offline archive guards for the storage-only migration bundle."""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tarfile

import pytest

spec = importlib.util.spec_from_file_location('storage_context', Path(__file__).resolve().parents[1] / 'scripts/prepare-storage-schema.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
PREFIX = 'controlplane_db-0.1.0/storage-schema/'


def fixture(tmp_path, extra=(), omit=None):
    archive = tmp_path / 'controlplane_db-0.1.0.tar.gz'
    with tarfile.open(archive, 'w:gz') as target:
        for name in sorted(module.FILES):
            if name == omit:
                continue
            item = tarfile.TarInfo(PREFIX + name)
            item.size = 4
            target.addfile(item, io.BytesIO(b'data'))
        for item in extra:
            target.addfile(item, io.BytesIO(b'x' * item.size) if item.isfile() else None)
    record = {'repository': 'hyperme-sh/controlplane-db', 'revision': 'a' * 40,
              'archive': archive.name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}
    (tmp_path / 'manifest.json').write_text(json.dumps(record))
    return archive, record


def test_copies_only_owner_bundle_and_records_source(tmp_path):
    other = tarfile.TarInfo('controlplane_db-0.1.0/migrations/old.py')
    other.size = 1
    _, record = fixture(tmp_path, [other])
    output = tmp_path / 'output'
    module.prepare(tmp_path, output)
    assert {p.name for p in output.iterdir()} == module.FILES | {'source.json'}
    assert json.loads((output / 'source.json').read_text()) == record
    with pytest.raises(FileExistsError):
        module.prepare(tmp_path, output)


def test_schema_remains_readable_to_non_owner_under_private_umask(tmp_path):
    fixture(tmp_path)
    output = tmp_path / 'output'
    previous = os.umask(0o077)
    try:
        module.prepare(tmp_path, output)
    finally:
        os.umask(previous)
    assert output.stat().st_mode & 0o777 == 0o755
    assert all(path.stat().st_mode & 0o777 == 0o644 for path in output.iterdir())


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'traversal', 'duplicate', 'unknown', 'oversize'])
def test_rejects_unsafe_members_before_writing(tmp_path, kind):
    item = tarfile.TarInfo(PREFIX + {'traversal': '../escape', 'unknown': 'extra.py'}.get(kind, 'migrate.py'))
    if kind in ('symlink', 'hardlink'):
        item.type = tarfile.SYMTYPE if kind == 'symlink' else tarfile.LNKTYPE
        item.linkname = '/etc/passwd'
    if kind == 'oversize':
        item.size = module.MAX_FILE + 1
    fixture(tmp_path, [item])
    output = tmp_path / 'output'
    with pytest.raises(ValueError):
        module.prepare(tmp_path, output)
    assert not output.exists()


def test_refuses_missing_member_and_changed_bytes(tmp_path):
    archive, _ = fixture(tmp_path, omit='migrate.py')
    with pytest.raises(ValueError, match='Incomplete'):
        module.prepare(tmp_path, tmp_path / 'output')
    archive.write_bytes(archive.read_bytes() + b'changed')
    with pytest.raises(ValueError, match='digest'):
        module.prepare(tmp_path, tmp_path / 'output')


@pytest.mark.parametrize('field,value', [('repository','other/db'), ('revision','main'), ('sha256','bad'), ('archive','../outside.tar.gz')])
def test_refuses_invalid_source_manifest(tmp_path, field, value):
    _, record = fixture(tmp_path)
    record[field] = value
    (tmp_path / 'manifest.json').write_text(json.dumps(record))
    with pytest.raises(ValueError, match='manifest'):
        module.prepare(tmp_path, tmp_path / 'output')
