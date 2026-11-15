import email.message
import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('notices', Path(__file__).resolve().parents[1] / 'scripts/prepare_notices.py')
notices = importlib.util.module_from_spec(spec)
spec.loader.exec_module(notices)


def distribution(root, files, name='example', version='1.0'):
    metadata = email.message.Message()
    metadata['Name'] = name
    for path, data in files.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return SimpleNamespace(metadata=metadata, version=version, files=list(files), locate_file=lambda path: root / path)


def test_preserves_british_and_bundled_license_names(tmp_path):
    dist = distribution(tmp_path / 'installed', {'example.dist-info/LICENCE.rst': b'terms',
                        'example.dist-info/licenses/vendor/terms.txt': b'vendor terms'})
    result = notices.prepare(tmp_path / 'out', tmp_path / 'supplements', [dist])
    assert len(result[0]['texts']) == 2
    for entry, record in result[0]['texts'].items():
        assert (tmp_path / 'out/example' / record['file']).read_bytes() == (tmp_path / 'installed' / entry).read_bytes()


def test_missing_license_text_fails(tmp_path):
    with pytest.raises(ValueError, match='No license text'):
        notices.prepare(tmp_path / 'out', tmp_path / 'supplements', [distribution(tmp_path, {})])


@pytest.mark.parametrize('change', ['none', 'version', 'digest', 'path', 'unused'])
def test_supplement_is_bound_to_version_and_bytes(tmp_path, change):
    root = tmp_path / 'supplements/example'
    root.mkdir(parents=True)
    (root / 'LICENSE').write_bytes(b'upstream terms')
    record = {'distribution': 'example', 'version': '1.0', 'files': {'LICENSE': hashlib.sha256(b'upstream terms').hexdigest()}}
    if change == 'version': record['version'] = '2.0'
    if change == 'digest': record['files']['LICENSE'] = '0' * 64
    if change == 'path': record['files'] = {'../LICENSE': record['files']['LICENSE']}
    if change == 'unused': record['distribution'] = 'other'
    (root / 'source.json').write_text(json.dumps(record))
    dist = distribution(tmp_path / 'installed', {'example.dist-info/LICENSE': b'installed terms'})
    if change == 'none':
        assert len(notices.prepare(tmp_path / 'out', root.parent, [dist])[0]['texts']) == 2
    else:
        with pytest.raises(ValueError):
            notices.prepare(tmp_path / 'out', root.parent, [dist])


verify_spec = importlib.util.spec_from_file_location('verify_notices', Path(__file__).resolve().parents[1] / 'scripts/verify_notices.py')
verify_notices = importlib.util.module_from_spec(verify_spec)
verify_spec.loader.exec_module(verify_notices)


@pytest.mark.parametrize('change', ['none', 'text', 'version', 'owner', 'installed'])
def test_final_bundle_matches_installed_bytes(tmp_path, change):
    owner = distribution(tmp_path / 'installed', {
        'controlplane_core-0.1.0.dist-info/licenses/LICENSE': b'owner license',
        'controlplane_core-0.1.0.dist-info/licenses/NOTICE': b'owner notice',
    }, name='controlplane-core', version='0.1.0')
    output = tmp_path / 'legal'
    rows = notices.prepare(output / 'third-party', tmp_path / 'supplements', [owner])
    (output / 'LICENSE').write_bytes(b'owner license')
    (output / 'NOTICE').write_bytes(b'owner notice')
    if change == 'text':
        record = next(iter(rows[0]['texts'].values()))
        (output / 'third-party/controlplane-core' / record['file']).write_bytes(b'changed')
    if change == 'version': owner.version = '0.2.0'
    if change == 'owner': (output / 'NOTICE').write_bytes(b'changed')
    if change == 'installed':
        Path(owner.locate_file('controlplane_core-0.1.0.dist-info/licenses/LICENSE')).write_bytes(b'changed')
    if change == 'none': verify_notices.verify(output, [owner])
    else:
        with pytest.raises(ValueError): verify_notices.verify(output, [owner])
