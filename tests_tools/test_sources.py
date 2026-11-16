import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('sources', Path(__file__).resolve().parents[1] / 'scripts/prepare_sources.py')
sources = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sources)


@pytest.fixture
def bundle(tmp_path):
    archive = tmp_path / 'upstream.tar.gz'
    archive.write_bytes(b'fixture source archive')
    native = tmp_path / 'psycopg_binary.libs/libexample.so.1'
    native.parent.mkdir()
    native.write_bytes(b'fixture native library')
    sbom = tmp_path / 'sbom.json'
    sbom.write_text('{}')
    manifest = {'schema_version': 1, 'publication_cleared': False,
        'archives': [{'filename': archive.name, 'url': 'https://files.pythonhosted.org/source.tar.gz',
                      'size': archive.stat().st_size, 'sha256': sources.digest(archive)}],
        'packages': [{'name': 'psycopg-binary', 'version': '3.3.7', 'sources': [archive.name]}],
        'binary_wheel': {'sbom_path': 'sbom.json', 'sbom_sha256': sources.digest(sbom),
            'members': [{'path': 'psycopg_binary.libs/libexample.so.1', 'sha256': sources.digest(native), 'source': None}]}}
    dist = SimpleNamespace(metadata={'Name': 'psycopg-binary', 'License-Expression': 'LGPL-3.0-only'},
                           version='3.3.7', files=['psycopg_binary.libs/libexample.so.1'],
                           locate_file=lambda p: tmp_path / p)
    def write():
        (tmp_path / 'index.json').write_text(json.dumps(manifest))
    return tmp_path, manifest, dist, write


def test_exact_source_and_native_pairing_retains_unresolved_status(bundle):
    root, _, dist, write = bundle
    write()
    result = sources.verify(root, [dist])
    assert result['unresolved_native'] == ['psycopg_binary.libs/libexample.so.1']
    assert result['publication_cleared'] is False


@pytest.mark.parametrize('change', ['source', 'version', 'native', 'sbom', 'inventory', 'new-package'])
def test_changes_require_source_review(bundle, change):
    root, manifest, dist, write = bundle
    packages = [dist]
    if change == 'source': (root / 'upstream.tar.gz').write_bytes(b'tampered')
    if change == 'version': dist.version = '3.3.8'
    if change == 'native': (root / 'psycopg_binary.libs/libexample.so.1').write_bytes(b'tampered')
    if change == 'sbom': (root / 'sbom.json').write_text('{"changed": true}')
    if change == 'inventory': dist.files.append('psycopg_binary.libs/extra.so')
    if change == 'new-package': packages.append(SimpleNamespace(metadata={'Name': 'new', 'License-Expression': 'MPL-2.0'}, version='1'))
    write()
    with pytest.raises(ValueError): sources.verify(root, packages)


@pytest.mark.parametrize('change', ['path', 'url', 'hash', 'size', 'duplicate', 'missing', 'native-path'])
def test_unsafe_or_incomplete_source_records_fail(bundle, change):
    _, manifest, _, _ = bundle
    if change == 'path': manifest['archives'][0]['filename'] = '../escape'
    if change == 'url': manifest['archives'][0]['url'] = 'https://private.invalid/source.tar.gz'
    if change == 'hash': manifest['archives'][0]['sha256'] = 'unverified'
    if change == 'size': manifest['archives'][0]['size'] = 0
    if change == 'duplicate': manifest['archives'].append(dict(manifest['archives'][0]))
    if change == 'missing': manifest['packages'][0]['sources'] = ['missing.tar.gz']
    if change == 'native-path': manifest['binary_wheel']['members'][0]['path'] = '../private.so'
    with pytest.raises(ValueError): sources.validate(manifest)


@pytest.mark.parametrize('target', ['http://files.pythonhosted.org/source',
                                    'https://private.invalid/source',
                                    'https://files.pythonhosted.org:444/source',
                                    'https://user:secret@files.pythonhosted.org/source'])
def test_redirects_are_rejected_before_following(target):
    request = sources.urllib.request.Request('https://files.pythonhosted.org/source')
    with pytest.raises(ValueError, match='redirect'):
        sources.ReviewedRedirects().redirect_request(request, None, 302, 'Found', {}, target)


def test_signed_cdn_redirects_are_narrowly_allowed():
    for host in sources.REDIRECT_HOSTS:
        target = f'https://{host}/source?signature=public-download'
        assert sources.allowed_url(target, redirect=True)
        assert not sources.allowed_url(target)
    assert not sources.allowed_url('https://private.invalid/source?signature=x', redirect=True)


def elf_fixture():
    import struct
    data = bytearray(300)
    data[:6] = b'\x7fELF\x02\x01'
    struct.pack_into('<Q', data, 40, 64)
    struct.pack_into('<HHH', data, 58, 64, 3, 1)
    names = b'\0.shstrtab\0.text\0'
    data[256:256 + len(names)] = names
    struct.pack_into('<IIQQQQIIQQ', data, 128, 1, 3, 0, 0, 256, len(names), 0, 0, 1, 0)
    struct.pack_into('<IIQQQQIIQQ', data, 192, 11, 1, 0, 0, 280, 4, 0, 0, 1, 0)
    data[280:284] = b'code'
    return data


def test_elf_code_hash_is_computed_without_loading():
    assert sources.elf_sections(elf_fixture(), ['.text']) == {'.text': hashlib.sha256(b'code').hexdigest()}


@pytest.mark.parametrize('change', ['header', 'table', 'bounds', 'name', 'missing'])
def test_invalid_elf_is_rejected(change):
    import struct
    data = elf_fixture()
    if change == 'header': data[0] = 0
    if change == 'table': struct.pack_into('<Q', data, 40, 9999)
    if change == 'bounds': struct.pack_into('<Q', data, 192 + 32, 9999)
    if change == 'name': struct.pack_into('<I', data, 192, 9999)
    with pytest.raises(ValueError):
        sources.elf_sections(data, ['.rodata'] if change == 'missing' else ['.text'])


@pytest.mark.parametrize('change', [None, 'metadata', 'recipe', 'config-link', 'original', 'installed'])
def test_libcrypt_provenance_chain(tmp_path, change):
    import io
    import tarfile
    raw = bytes(elf_fixture())
    layer = tmp_path / 'layer.tar.gz'
    with tarfile.open(layer, 'w:gz') as archive:
        member = tarfile.TarInfo('libcrypt.so.1'); member.size = len(raw)
        archive.addfile(member, io.BytesIO(raw))
    config = {'history': [{'empty_layer': True, 'created_by': 'metadata'},
                         {'created_by': 'LIBXCRYPT_VERSION=4.5.2 LIBXCRYPT_HASH=' + 'a' * 64}]}
    if change == 'recipe': config['history'][1]['created_by'] = 'different source'
    provenance = tmp_path / 'provenance'; provenance.mkdir()
    config_path = provenance / 'config.json'; config_path.write_text(json.dumps(config))
    image = {'config': {'digest': 'sha256:' + sources.digest(config_path)},
             'layers': [{'digest': 'sha256:' + sources.digest(layer)}]}
    if change == 'config-link': image['config']['digest'] = 'sha256:' + 'b' * 64
    image_path = provenance / 'image.json'; image_path.write_text(json.dumps(image))
    record = {'image_manifest': image_path.name, 'image_manifest_sha256': sources.digest(image_path),
              'image_config': config_path.name, 'image_config_sha256': sources.digest(config_path),
              'version': '4.5.2', 'source_archive': 'source.tar.gz', 'original_layer': layer.name,
              'original_member': 'libcrypt.so.1', 'original_sha256': hashlib.sha256(raw).hexdigest(),
              'wheel_member': 'libcrypt.so.1', 'sections': sources.elf_sections(raw, ['.text'])}
    manifest = {'libcrypt_provenance': record, 'archives': [
        {'filename': 'source.tar.gz', 'sha256': 'a' * 64},
        {'filename': layer.name, 'sha256': sources.digest(layer)}]}
    installed = tmp_path / 'installed.so'; installed.write_bytes(raw)
    if change == 'metadata': config_path.write_text('{}')
    if change == 'original': record['original_sha256'] = 'b' * 64
    if change == 'installed':
        altered = bytearray(raw); altered[280] = 1; installed.write_bytes(altered)
    binary = SimpleNamespace(locate_file=lambda name: installed)
    if change:
        with pytest.raises(ValueError): sources.verify_libcrypt(tmp_path, manifest, binary)
    else:
        sources.verify_libcrypt(tmp_path, manifest, binary)


def test_source_records_match_the_frozen_core_lock():
    import tomllib
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'licenses/sources.json').read_text())
    locked = {row['name']: row for row in tomllib.loads((root / 'uv.lock').read_text())['package']}
    sources.validate(manifest)
    for row in manifest['packages']:
        assert locked[row['name']]['version'] == row['version']
    assert any(row['hash'] == 'sha256:' + manifest['binary_wheel']['sha256']
               for row in locked['psycopg-binary']['wheels'])
    assert not any(row['source'] is None for row in manifest['binary_wheel']['members'])


def test_quay_s3_fallback_is_restricted_to_the_reviewed_layer():
    url = 'https://s3.us-east-1.amazonaws.com' + sources.QUAY_LAYER_PATH + '?signature=public-download'
    assert sources.allowed_url(url, redirect=True)
    assert not sources.allowed_url(url)
    assert not sources.allowed_url(url.replace('quayio-production-s3', 'another-bucket'), redirect=True)
    assert not sources.allowed_url(url.replace('0ed13069', 'bad13069'), redirect=True)
    assert not sources.allowed_url(url.replace('https:', 'http:'), redirect=True)
