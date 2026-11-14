"""Copy only the storage owner's files from a digest-verified source archive."""
import hashlib
import io
import json
from pathlib import Path
import re
import tarfile


FILES = frozenset({'001_storage.sql', 'migrate.py', 'README.md', '002_media_objects.sql', 'media_migrate.py', 'MEDIA.md'})
MAX_ARCHIVE = 16 * 1024 * 1024
MAX_FILE = 128 * 1024


def prepare(directory, output):
    directory, output = Path(directory), Path(output)
    record = json.loads((directory / 'manifest.json').read_text())
    if (record.get('repository') != 'hyperme-sh/controlplane-db'
            or not re.fullmatch('[0-9a-f]{40}', record.get('revision', ''))
            or not re.fullmatch('[0-9a-f]{64}', record.get('sha256', ''))
            or not re.fullmatch(r'controlplane_db-[0-9]+\.[0-9]+\.[0-9]+\.tar\.gz', record.get('archive', ''))):
        raise ValueError('Invalid storage schema source manifest')
    archive = directory / record['archive']
    if archive.is_symlink() or archive.stat().st_size > MAX_ARCHIVE:
        raise ValueError('Invalid storage schema source archive')
    data = archive.read_bytes()
    if hashlib.sha256(data).hexdigest() != record['sha256']:
        raise ValueError('Storage schema source digest differs')
    prefix = record['archive'][:-7] + '/storage-schema/'
    selected = {}
    expanded = 0
    with tarfile.open(fileobj=io.BytesIO(data), mode='r:gz') as source:
        for count, member in enumerate(source, 1):
            expanded += member.size
            if count > 10000 or expanded > 64 * 1024 * 1024:
                raise ValueError('Storage schema archive exceeds extraction limits')
            if not member.name.startswith(prefix):
                continue
            name = member.name[len(prefix):]
            if not name and member.isdir():
                continue
            if (name not in FILES or name in selected or not member.isfile()
                    or not 0 < member.size <= MAX_FILE):
                raise ValueError('Invalid storage schema member')
            stream = source.extractfile(member)
            selected[name] = stream.read(MAX_FILE + 1)
            if len(selected[name]) != member.size:
                raise ValueError('Invalid storage schema member size')
    if selected.keys() != FILES:
        raise ValueError('Incomplete storage schema bundle')
    output.mkdir(mode=0o755, parents=True, exist_ok=False)
    # Public migration source must remain readable after root-owned Docker COPY
    # into an image that executes as uid 65532, even with a restrictive umask.
    output.chmod(0o755)
    for name, content in selected.items():
        (output / name).write_bytes(content)
        (output / name).chmod(0o644)
    (output / 'source.json').write_text(json.dumps(record, sort_keys=True, indent=2) + '\n')
    (output / 'source.json').chmod(0o644)


if __name__ == '__main__':
    import sys
    prepare(sys.argv[1], sys.argv[2])
