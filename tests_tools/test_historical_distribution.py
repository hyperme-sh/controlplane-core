import importlib.util
import json
from pathlib import Path
import sys
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import inventory_registry as registry
import verify_history_review as history


def test_registry_pagination_cannot_escape():
    for url in ['https://evil.invalid/v2/hyperme-sh/controlplane-core/storage/tags/list','https://ghcr.io/v2/other/runtime/tags/list','https://ghcr.io/v2/hyperme-sh/controlplane-core/storage/../other']:
        with pytest.raises(ValueError):registry.registry_url(url)


def test_authorized_empty_inventory_is_distinct_from_request_failure():
    result=registry.inventory(lambda url:({'tags':[]},{},b''))
    assert result['images']==[]
    def denied(url):raise PermissionError('denied')
    with pytest.raises(PermissionError):registry.inventory(denied)


def test_registry_manifest_digest_must_match():
    def request(url):
        if '/tags/' in url:return {'tags':['test']},{},b''
        return {},{'Docker-Content-Digest':'sha256:'+'a'*64},b'{}'
    with pytest.raises(ValueError,match='manifest bytes'):registry.inventory(request)


def test_new_historical_coordinate_refuses():
    with pytest.raises(ValueError,match='inventory'):
        history.verify({'history_findings':[{'object':'a','path':'b'}]},{'publication_cleared':False,'findings':[]})


def test_changed_historical_bytes_refuse(monkeypatch):
    r={'object':'a','path':'b','blob_sha256':'0'*64,'content_sha256':'0'*64,'classification':'legacy-provider-settings'}
    monkeypatch.setattr(history.subprocess,'check_output',lambda *a:b'changed')
    with pytest.raises(ValueError,match='blob'):
        history.verify({'history_findings':[r]},{'publication_cleared':False,'findings':[r]})


def test_old_layer_inspection_rejects_traversal_and_observes_root_user(tmp_path):
    import io
    import tarfile
    spec=importlib.util.spec_from_file_location('inspect_old',ROOT/'scripts/inspect-container.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    def image(path,member):
        layer=io.BytesIO()
        with tarfile.open(fileobj=layer,mode='w') as t:
            item=tarfile.TarInfo(member);item.size=4;t.addfile(item,io.BytesIO(b'data'))
        with tarfile.open(path,'w') as t:
            for name,data in [('manifest.json',json.dumps([{'Config':'config.json','Layers':['layer.tar']}]).encode()),('config.json',b'{"config":{"User":"0"}}'),('layer.tar',layer.getvalue())]:
                item=tarfile.TarInfo(name);item.size=len(data);t.addfile(item,io.BytesIO(data))
    archive=tmp_path/'image.tar';image(archive,'safe.txt')
    result=module.unpack_image(archive,tmp_path/'safe',enforce_user=False)
    assert result['user']=='0'
    assert (tmp_path/'safe/0/safe.txt').read_bytes()==b'data'
    image(archive,'../escape')
    with pytest.raises(ValueError,match='Unsafe'):
        module.unpack_image(archive,tmp_path/'bad',enforce_user=False)


def test_retained_untagged_versions_are_included_and_deduplicated():
    raw = b'{}'
    import hashlib
    digest = 'sha256:' + hashlib.sha256(raw).hexdigest()
    versions = registry.retained_versions(lambda page: [{'name': digest}, {'name': digest}])
    def request(url):
        if '/tags/' in url: return {'tags': []}, {}, b''
        return {}, {'Docker-Content-Digest': digest}, raw
    result = registry.inventory(request, versions)
    assert result['retained_version_count'] == 1
    assert result['images'][0]['digest'] == digest


def test_version_lookup_failure_is_not_an_empty_inventory():
    def denied(page): raise PermissionError('denied')
    with pytest.raises(PermissionError): registry.retained_versions(denied)
    with pytest.raises(ValueError): registry.retained_versions(lambda page: [{'name': 'not-a-digest'}])
