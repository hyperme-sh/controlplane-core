"""Fetch hash-bound upstream sources and verify their installed-image pairing."""
import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path, PurePosixPath
import re
import struct
import tarfile
import urllib.parse
import urllib.request

HOSTS = {'www.python.org', 'files.pythonhosted.org', 'codeload.github.com', 'vault.centos.org', 'www.openldap.org', 'github.com', 'quay.io'}
REDIRECT_HOSTS = {'www.python.org', 'release-assets.githubusercontent.com', 'cdn01.quay.io'}


def allowed_url(url, redirect=False):
    parsed = urllib.parse.urlsplit(url)
    return (parsed.scheme == 'https' and (parsed.hostname in HOSTS or (redirect and parsed.hostname in REDIRECT_HOSTS))
            and parsed.port in (None, 443) and not parsed.username and not parsed.password
            and (not parsed.query or (redirect and parsed.hostname in REDIRECT_HOSTS)) and not parsed.fragment)


class ReviewedRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        if not allowed_url(newurl, redirect=True):
            raise ValueError('Source redirect escaped reviewed HTTPS hosts: ' + str(urllib.parse.urlsplit(newurl).hostname))
        return super().redirect_request(request, response, code, message, headers, newurl)


def canonical(name):
    return re.sub(r'[-_.]+', '-', name).lower()


def validate(manifest):
    if manifest['schema_version'] != 1 or manifest['publication_cleared'] is not False:
        raise ValueError('Unsupported source preparation policy')
    seen = set()
    for row in manifest['archives']:
        name = row['filename']
        if (Path(name).name != name or not name or name in seen
                or not allowed_url(row['url'])
                or not re.fullmatch('[0-9a-f]{64}', row['sha256'])
                or not 0 < row['size'] < 200 * 1024 * 1024):
            raise ValueError('Invalid source archive record')
        seen.add(name)
    packages = {canonical(row['name']) for row in manifest['packages']}
    if len(packages) != len(manifest['packages']):
        raise ValueError('Duplicate source package')
    for row in manifest['packages']:
        if not row['sources'] or set(row['sources']) - seen:
            raise ValueError('Package source archive is missing')
    for row in manifest['binary_wheel']['members']:
        path = PurePosixPath(row['path'])
        if (path.is_absolute() or '..' in path.parts or not path.parts
                or path.parts[0] not in ('psycopg_binary', 'psycopg_binary.libs')
                or (row['source'] is not None and row['source'] not in seen)):
            raise ValueError('Invalid native source mapping')


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch(manifest_path, output):
    manifest = json.loads(manifest_path.read_text())
    validate(manifest)
    output.mkdir(parents=True, exist_ok=False)
    opener = urllib.request.build_opener(ReviewedRedirects())
    for row in manifest['archives']:
        print('Fetching reviewed source: ' + row['filename'], flush=True)
        target = output / row['filename']
        with opener.open(row['url'], timeout=60) as response, target.open('xb') as stream:
            if not allowed_url(response.url, redirect=True):
                raise ValueError('Source redirect escaped reviewed hosts')
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > row['size']:
                    raise ValueError('Source exceeds reviewed size')
                stream.write(chunk)
        if target.stat().st_size != row['size'] or digest(target) != row['sha256']:
            raise ValueError('Upstream source bytes changed: ' + row['filename'])
    (output / 'index.json').write_text(json.dumps(manifest, indent=2) + '\n')


def elf_sections(data, names):
    """Read bounded ELF64 little-endian sections without loading native code."""
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01':
        raise ValueError('Expected ELF64 little-endian library')
    offset = struct.unpack_from('<Q', data, 40)[0]
    size, count, strings = struct.unpack_from('<HHH', data, 58)
    if size != 64 or not count or strings >= count or offset + size * count > len(data):
        raise ValueError('Invalid ELF section table')
    def section(index):
        row = struct.unpack_from('<IIQQQQIIQQ', data, offset + index * size)
        start, length = row[4:6]
        if start + length > len(data):
            raise ValueError('Invalid ELF section bounds')
        return row[0], data[start:start + length]
    _, table = section(strings)
    result = {}
    for index in range(count):
        # NOBITS sections have no bytes in the file.
        if struct.unpack_from('<I', data, offset + index * size + 4)[0] == 8:
            continue
        name, content = section(index)
        end = table.find(b'\0', name)
        if name >= len(table) or end < 0:
            raise ValueError('Invalid ELF section name')
        label = table[name:end].decode('ascii')
        if label in names:
            if label in result:
                raise ValueError('Duplicate ELF section')
            result[label] = hashlib.sha256(content).hexdigest()
    if set(result) != set(names):
        raise ValueError('Missing ELF section')
    return result


def verify_libcrypt(directory, manifest, binary):
    record = manifest['libcrypt_provenance']
    documents = {}
    for key in ('image_manifest', 'image_config'):
        name = record[key]
        if Path(name).name != name:
            raise ValueError('Invalid provenance path')
        path = directory / 'provenance' / name
        if digest(path) != record[key + '_sha256']:
            raise ValueError('Native build metadata changed')
        documents[key] = json.loads(path.read_text())
    image, config = documents['image_manifest'], documents['image_config']
    if image['config']['digest'] != 'sha256:' + record['image_config_sha256']:
        raise ValueError('Native build config does not match image')
    archives = {r['filename']: r for r in manifest['archives']}
    source = archives[record['source_archive']]
    layer = archives[record['original_layer']]
    history = [r for r in config['history'] if not r.get('empty_layer', False)]
    if len(history) != len(image['layers']):
        raise ValueError('Native build layer count changed')
    matches = [h['created_by'] for h, entry in zip(history, image['layers'])
               if entry['digest'] == 'sha256:' + layer['sha256']]
    if (len(matches) != 1 or 'LIBXCRYPT_VERSION=' + record['version'] not in matches[0]
            or 'LIBXCRYPT_HASH=' + source['sha256'] not in matches[0]):
        raise ValueError('Native source does not match build recipe')
    with tarfile.open(directory / record['original_layer']) as archive:
        member = archive.getmember(record['original_member'])
        if not member.isfile() or member.size > 20 * 1024 * 1024:
            raise ValueError('Invalid original native member')
        original = archive.extractfile(member).read()
    if hashlib.sha256(original).hexdigest() != record['original_sha256']:
        raise ValueError('Original native library changed')
    installed = Path(binary.locate_file(record['wheel_member'])).read_bytes()
    for data in (original, installed):
        if elf_sections(data, record['sections']) != record['sections']:
            raise ValueError('Native code sections differ from build')


def verify(directory, distributions=None):
    manifest = json.loads((directory / 'index.json').read_text())
    validate(manifest)
    for row in manifest['archives']:
        path = directory / row['filename']
        if path.stat().st_size != row['size'] or digest(path) != row['sha256']:
            raise ValueError('Installed source bytes differ: ' + row['filename'])
    installed = {canonical(d.metadata['Name']): d for d in
                 (distributions if distributions is not None else importlib.metadata.distributions())}
    expected = {canonical(row['name']): row['version'] for row in manifest['packages']}
    for name, version in expected.items():
        if name not in installed or installed[name].version != version:
            raise ValueError('Source version differs from installed package: ' + name)
    for name, dist in installed.items():
        classifiers = getattr(dist.metadata, 'get_all', lambda key, default: default)('Classifier', [])
        expression = ' '.join([dist.metadata.get('License-Expression') or '',
                               dist.metadata.get('License') or '', *classifiers])
        if re.search(r'\b(?:LGPL|AGPL|GPL|MPL)(?:[- v]|[0-9])', expression, re.I) and name not in expected:
            raise ValueError('New copyleft package needs source review: ' + name)
    binary = installed['psycopg-binary']
    wheel = manifest['binary_wheel']
    paths = {str(p) for p in binary.files or [] if
             (str(p).startswith('psycopg_binary.libs/') or str(p).startswith('psycopg_binary/'))
             and '.so' in Path(str(p)).name}
    expected_paths = {row['path'] for row in wheel['members']}
    if paths != expected_paths:
        raise ValueError('Native file inventory changed')
    if digest(Path(binary.locate_file(wheel['sbom_path']))) != wheel['sbom_sha256']:
        raise ValueError('Native SBOM changed')
    for row in wheel['members']:
        if digest(Path(binary.locate_file(row['path']))) != row['sha256']:
            raise ValueError('Native library bytes changed: ' + row['path'])
    if 'libcrypt_provenance' in manifest:
        verify_libcrypt(directory, manifest, binary)
    return {'packages': len(expected), 'archives': len(manifest['archives']),
            'unresolved_native': [r['path'] for r in wheel['members'] if r['source'] is None],
            'publication_cleared': False}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['fetch', 'verify'])
    parser.add_argument('path', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.mode == 'fetch':
        fetch(args.path, args.output)
    else:
        print(json.dumps(verify(args.path), indent=2))
