"""Classify exact public-source vectors; never exempt runtime or changed bytes."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile


def classify(findings, layers, manifest, baseline):
    archives = {r['filename']: r for r in manifest['archives']}
    known = {(r['archive'], r['member']): r for r in baseline['members']}
    candidates = []
    unexpected = []
    required = {}
    for finding in findings:
        archive_path, separator, member = finding['File'].partition('!')
        path = Path(archive_path).resolve()
        try:
            rel = path.relative_to(layers.resolve())
        except ValueError:
            unexpected.append(finding)
            continue
        valid_path = len(rel.parts) == 1
        record = known.get((path.name, member))
        if (not separator or not valid_path or path.name not in archives or not record
                or finding['StartLine'] not in record['rules'].get(finding['RuleID'], [])):
            unexpected.append(finding)
            continue
        required.setdefault(path, set()).add(member)
        candidates.append((finding, path, member, record['sha256']))
    valid = set()
    for path, members in required.items():
        with path.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != archives[path.name]['sha256']:
            continue
        # One sequential decompression per archive; no links or source are executed.
        with tarfile.open(path, mode='r|*') as archive:
            for entry in archive:
                if entry.isfile() and entry.name in members:
                    digest = hashlib.sha256(archive.extractfile(entry).read()).hexdigest()
                    valid.add((path, entry.name, digest))
    accepted = 0
    for finding, path, member, digest in candidates:
        if (path, member, digest) in valid:
            accepted += 1
        else:
            unexpected.append(finding)
    return accepted, unexpected


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('findings', type=Path)
    parser.add_argument('layers', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('baseline', type=Path)
    args = parser.parse_args()
    accepted, unexpected = classify(json.loads(args.findings.read_text()), args.layers,
        json.loads(args.manifest.read_text()), json.loads(args.baseline.read_text()))
    print(json.dumps({'known_public_source_findings': accepted, 'unexpected_findings': len(unexpected)}))
    for finding in unexpected:
        print(json.dumps({key: finding.get(key) for key in ('File', 'StartLine', 'RuleID')}))
    raise SystemExit(1 if unexpected else 0)
