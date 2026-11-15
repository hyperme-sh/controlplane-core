"""Verify every installed dependency and owner legal text against the bundle."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys


def verify(directory, distributions=None):
    distributions = list(distributions if distributions is not None else importlib.metadata.distributions())
    index = json.loads((directory / 'third-party/index.json').read_text())
    expected = {(d.metadata['Name'], d.version) for d in distributions}
    actual = {(row['name'], row['version']) for row in index}
    if actual != expected or len(actual) != len(index):
        raise ValueError('Notice inventory differs from installed distributions')
    installed = {(d.metadata['Name'], d.version): d for d in distributions}
    for row in index:
        key = re.sub(r'[-_.]+', '-', row['name']).lower()
        if not re.fullmatch('[a-z0-9][a-z0-9-]*', key) or not row['texts']:
            raise ValueError('Invalid notice identity or empty text inventory')
        dist = installed[row['name'], row['version']]
        for original, record in row['texts'].items():
            filename = record['file']
            if Path(filename).name != filename:
                raise ValueError('Unsafe notice filename')
            data = (directory / 'third-party' / key / filename).read_bytes()
            if hashlib.sha256(data).hexdigest() != record['sha256']:
                raise ValueError('Notice bytes differ from index')
            if not original.startswith('upstream/') and data != Path(dist.locate_file(original)).read_bytes():
                raise ValueError('Notice bytes differ from installed dependency')
    owner, = (d for d in distributions if d.metadata['Name'] == 'controlplane-core')
    for name in ('LICENSE', 'NOTICE'):
        entry, = (p for p in owner.files if str(p).endswith('.dist-info/licenses/' + name))
        data = (directory / name).read_bytes()
        if not data or data != Path(owner.locate_file(entry)).read_bytes():
            raise ValueError('Owner legal text differs from qualified package')
    print(f'Verified notice bytes for {len(index)} installed distributions')


if __name__ == '__main__':
    verify(Path(sys.argv[1]))
