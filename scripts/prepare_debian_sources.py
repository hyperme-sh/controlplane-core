"""Bundle exact Debian source descriptors and checksum-bound archive bytes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import re
import urllib.parse
import urllib.request

HOSTS = {'deb.debian.org', 'security.debian.org', 'snapshot.debian.org'}

class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Debian source redirect refused')


def digest(path):
    with path.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def records(manifest, base):
    if manifest['schema_version'] != 1 or manifest['publication_cleared'] is not False:
        raise ValueError('Unsupported Debian source policy')
    expected = {(p['source'], p['source_version']) for p in base['packages']}
    actual = [(p['source'], p['version']) for p in manifest['sources']]
    if set(actual) != expected or len(actual) != len(expected):
        raise ValueError('Debian source inventory differs from base')
    files = {}
    for source in manifest['sources']:
        if not source['archives']:
            raise ValueError('Missing source archives')
        for row in [source['descriptor'], *source['archives']]:
            name = row['filename']; url = urllib.parse.urlsplit(row['url'])
            if (Path(name).name != name or not name or name in ('.', '..')
                    or not re.fullmatch('[a-f0-9]{64}', row['sha256'])
                    or not 0 < row['size'] < 200 * 1024 * 1024
                    or url.scheme != 'https' or url.hostname not in HOSTS
                    or (url.hostname == 'snapshot.debian.org' and not re.fullmatch(r'/file/[a-f0-9]{40}', url.path))
                    or url.port not in (None, 443) or url.username or url.password or url.query or url.fragment):
                raise ValueError('Invalid Debian source record')
            if name in files and files[name] != row:
                raise ValueError('Conflicting Debian archive identity')
            files[name] = row
    return files


def verify(directory, manifest, base):
    files = records(manifest, base)
    for name, row in files.items():
        path = directory / name
        if path.stat().st_size != row['size'] or digest(path) != row['sha256']:
            raise ValueError('Debian source bytes changed: ' + name)
    for source in manifest['sources']:
        text = (directory / source['descriptor']['filename']).read_text()
        for field, expected in [('Source', source['source']), ('Version', source['version'])]:
            if re.findall(r'^' + field + r': (.+)$', text, re.M) != [expected]:
                raise ValueError('Debian descriptor identity differs')
        sections = text.split('Checksums-Sha256:\n')
        if len(sections) != 2:
            raise ValueError('Ambiguous descriptor checksums')
        declared = []
        for line in sections[1].splitlines():
            match = re.fullmatch(r' ([a-f0-9]{64}) +(\d+) +(\S+)', line)
            if not match:
                break
            declared.append((match[3], match[1], int(match[2])))
        expected = [(a['filename'], a['sha256'], a['size']) for a in source['archives']]
        if sorted(declared) != sorted(expected) or len(set(declared)) != len(declared):
            raise ValueError('Archive inventory differs from descriptor checksums')
    return {'debian_sources': len(manifest['sources']), 'source_files': len(files), 'publication_cleared': False}


def fetch(manifest_path, base_path, output):
    manifest = json.loads(manifest_path.read_text()); base = json.loads(base_path.read_text())
    files = records(manifest, base)
    output.mkdir(parents=True, exist_ok=False)
    def download(row):
        opener = urllib.request.build_opener(NoRedirects())
        path = output / row['filename']
        with opener.open(row['url'], timeout=90) as response, path.open('xb') as stream:
            count = 0
            while chunk := response.read(1024 * 1024):
                count += len(chunk)
                if count > row['size']:
                    raise ValueError('Debian source exceeds reviewed size')
                stream.write(chunk)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, files.values()))
    result = verify(output, manifest, base)
    (output / 'index.json').write_text(json.dumps(manifest, indent=2)+'\n')
    (output / 'base-image.json').write_text(json.dumps(base, indent=2)+'\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['fetch', 'verify'])
    parser.add_argument('path', type=Path)
    parser.add_argument('--base', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    if args.mode == 'fetch':
        result = fetch(args.path, args.base, args.output)
    else:
        result = verify(args.path, json.loads((args.path/'index.json').read_text()), json.loads((args.path/'base-image.json').read_text()))
    print(json.dumps(result))
