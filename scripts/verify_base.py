"""Verify pinned base layers, installed legal bytes and CPython source pairing."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def digest(data):
    return hashlib.sha256(data).hexdigest()


def verify(root, manifest, sources):
    if manifest['publication_cleared'] is not False:
        raise ValueError('Base publication must remain uncleared')
    if digest((root / 'var/lib/dpkg/status').read_bytes()) != manifest['status_sha256']:
        raise ValueError('Base installed package inventory changed')
    for path, expected in manifest['legal_sha256'].items():
        resolved = (root / path).resolve()
        if not resolved.is_relative_to(root.resolve()) or digest(resolved.read_bytes()) != expected:
            raise ValueError('Base legal text changed: ' + path)
    record = manifest['python']
    archive = sources / ('Python-' + record['version'] + '.tar.xz')
    if digest(archive.read_bytes()) != record['source_sha256']:
        raise ValueError('CPython source changed')
    with tarfile.open(archive) as stream:
        member = stream.getmember('Python-' + record['version'] + '/LICENSE')
        if not member.isfile() or member.size > 1024 * 1024:
            raise ValueError('Invalid CPython license member')
        if digest(stream.extractfile(member).read()) != manifest['legal_sha256']['usr/local/lib/python3.13/LICENSE.txt']:
            raise ValueError('CPython source/license pairing changed')
    return {'base_packages': len(manifest['packages']), 'legal_files': len(manifest['legal_sha256']), 'publication_cleared': False}


def verify_layers(config, manifest):
    expected = manifest['diff_ids']
    if not expected or config['rootfs']['diff_ids'][:len(expected)] != expected:
        raise ValueError('Prepared image does not retain the reviewed base layers')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['installed', 'layers'])
    parser.add_argument('manifest', type=Path)
    parser.add_argument('path', type=Path)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    if args.mode == 'layers':
        verify_layers(json.loads(args.path.read_text()), manifest)
    else:
        print(json.dumps(verify(Path('/'), manifest, args.path)))
