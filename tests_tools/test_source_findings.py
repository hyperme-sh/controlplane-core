import hashlib
import importlib.util
import io
from pathlib import Path
import tarfile

import pytest

spec = importlib.util.spec_from_file_location('source_findings', Path(__file__).resolve().parents[1] / 'scripts/classify_source_findings.py')
classifier = importlib.util.module_from_spec(spec)
spec.loader.exec_module(classifier)


@pytest.fixture
def case(tmp_path):
    archive = tmp_path / 'bundle/upstream.tar.gz'
    archive.parent.mkdir(parents=True)
    data = b'synthetic public test vector'
    with tarfile.open(archive, 'w:gz') as tar:
        entry = tarfile.TarInfo('upstream/test/vector.txt')
        entry.size = len(data)
        tar.addfile(entry, io.BytesIO(data))
    findings = [{'File': str(archive) + '!upstream/test/vector.txt', 'StartLine': 1, 'RuleID': 'private-key'}]
    manifest = {'archives': [{'filename': archive.name, 'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()}]}
    baseline = {'members': [{'archive': archive.name, 'member': 'upstream/test/vector.txt',
                 'sha256': hashlib.sha256(data).hexdigest(), 'rules': {'private-key': [1]}}]}
    return archive.parent, archive, findings, manifest, baseline


def test_exact_public_source_coordinates_are_classified(case):
    root, _, findings, manifest, baseline = case
    assert classifier.classify(findings, root, manifest, baseline) == (1, [])


@pytest.mark.parametrize('change', ['archive', 'member', 'line', 'rule', 'runtime'])
def test_changed_or_runtime_material_is_never_exempt(case, change):
    root, archive, findings, manifest, baseline = case
    if change == 'archive': archive.write_bytes(archive.read_bytes() + b'changed')
    if change == 'member': baseline['members'][0]['sha256'] = '0' * 64
    if change == 'line': findings[0]['StartLine'] = 2
    if change == 'rule': findings[0]['RuleID'] = 'other-rule'
    if change == 'runtime': findings[0]['File'] = str(root / '1/opt/runtime/secret.txt')
    accepted, unexpected = classifier.classify(findings, root, manifest, baseline)
    assert accepted == 0 and unexpected == findings
