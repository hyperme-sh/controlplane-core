"""Classify only reviewed non-secret history bytes and exact source archives."""
import hashlib
import json
from pathlib import Path
import sys
from classify_source_findings import classify as source_matches
from classify_debian_findings import classify as debian_matches


def classify(rows, layers, root):
    known = json.loads((root / '.github/workflows/history-scan-review.json').read_text())
    coordinates = {(r['sha256'], r['rule'], r['start'], r['end']) for r in known}
    unexpected = []
    accepted = 0
    for row in rows:
        path = Path(row['File'])
        if (path.resolve() == (layers / 'image-config.json').resolve()
                and row['RuleID'] == 'generic-api-key'
                and row.get('Secret') == '7169605F62C751356D054A26A821E680E5FA6305'):
            accepted += 1
            continue
        if path.is_file() and path.resolve().is_relative_to(layers.resolve()) and 'site-packages' in path.parts:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if (digest, row['RuleID'], row['StartLine'], row['EndLine']) in coordinates:
                accepted += 1
                continue
        unexpected.append(row)
    for directory in layers.glob('*/usr/share/doc/controlplane-core/sources'):
        count, unexpected = source_matches(unexpected, directory,
            json.loads((root / 'licenses/sources.json').read_text()),
            json.loads((root / '.github/workflows/source-scan-baseline.json').read_text()))
        accepted += count
    for directory in layers.glob('*/usr/share/doc/controlplane-core/debian-sources'):
        count, unexpected = debian_matches(unexpected, directory,
            json.loads((root / 'licenses/debian-sources.json').read_text()),
            json.loads((root / '.github/workflows/debian-source-scan-baseline.json').read_text()))
        accepted += count
    return accepted, unexpected


if __name__ == '__main__':
    rows = json.loads(Path(sys.argv[1]).read_text())
    accepted, unexpected = classify(rows, Path(sys.argv[2]), Path(__file__).resolve().parents[1])
    print(json.dumps({'reviewed_non_secret_matches': accepted, 'unexpected_matches': len(unexpected)}))
    for row in unexpected:
        print(json.dumps({key: row.get(key) for key in ('File', 'StartLine', 'EndLine', 'RuleID')}))
    raise SystemExit(bool(unexpected))
