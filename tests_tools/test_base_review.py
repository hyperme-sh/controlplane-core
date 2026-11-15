import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from verify_base import verify_layers, verify, digest


def test_base_prefix_allows_consumer_layers_but_refuses_changed_base():
    m={'diff_ids':['a','b']}
    verify_layers({'rootfs':{'diff_ids':['a','b','c']}},m)
    with pytest.raises(ValueError):verify_layers({'rootfs':{'diff_ids':['a','x']}},m)


def test_changed_base_inventory_refuses_before_source_read(tmp_path):
    p=tmp_path/'var/lib/dpkg/status';p.parent.mkdir(parents=True);p.write_bytes(b'changed')
    with pytest.raises(ValueError,match='inventory'):
        verify(tmp_path,{'publication_cleared':False,'status_sha256':digest(b'original')},tmp_path)


def test_legal_path_escape_refuses(tmp_path):
    p=tmp_path/'var/lib/dpkg/status';p.parent.mkdir(parents=True);p.write_bytes(b'status')
    m={'publication_cleared':False,'status_sha256':digest(b'status'),'legal_sha256':{'../outside':'x'}}
    with pytest.raises(ValueError,match='legal'):
        verify(tmp_path,m,tmp_path)


def test_python_source_and_installed_license_pairing(tmp_path):
    import io
    import tarfile
    root = tmp_path / 'root'
    status = root / 'var/lib/dpkg/status'
    status.parent.mkdir(parents=True)
    status.write_bytes(b'installed inventory')
    legal = root / 'usr/local/lib/python3.13/LICENSE.txt'
    legal.parent.mkdir(parents=True)
    legal.write_bytes(b'upstream terms')
    source = tmp_path / 'Python-3.13.18.tar.xz'
    with tarfile.open(source, 'w:xz') as archive:
        member = tarfile.TarInfo('Python-3.13.18/LICENSE')
        member.size = len(b'upstream terms')
        archive.addfile(member, io.BytesIO(b'upstream terms'))
    manifest = {'publication_cleared': False, 'status_sha256': digest(status.read_bytes()),
                'legal_sha256': {'usr/local/lib/python3.13/LICENSE.txt': digest(legal.read_bytes())},
                'python': {'version': '3.13.18', 'source_sha256': digest(source.read_bytes())},
                'packages': []}
    assert verify(root, manifest, tmp_path)['legal_files'] == 1
    legal.write_bytes(b'changed')
    with pytest.raises(ValueError, match='legal text'):
        verify(root, manifest, tmp_path)
    # Even an updated legal-file hash must still match the reviewed source LICENSE.
    manifest['legal_sha256']['usr/local/lib/python3.13/LICENSE.txt'] = digest(legal.read_bytes())
    with pytest.raises(ValueError, match='pairing'):
        verify(root, manifest, tmp_path)
    source.write_bytes(b'changed source')
    with pytest.raises(ValueError, match='source changed'):
        verify(root, manifest, tmp_path)
