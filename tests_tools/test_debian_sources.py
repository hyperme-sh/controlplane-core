import hashlib
import json
from pathlib import Path
import sys
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from prepare_debian_sources import records, verify, NoRedirects

@pytest.fixture
def bundle(tmp_path):
    data=b'public source archive'
    archive={'filename':'example.tar.xz','url':'https://deb.debian.org/debian/pool/main/e/example/example.tar.xz','sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}
    (tmp_path/archive['filename']).write_bytes(data)
    desc=f"Source: example\nVersion: 1:2.0-1\nChecksums-Sha256:\n {archive['sha256']} {archive['size']} {archive['filename']}\nFiles:\n".encode()
    descriptor={'filename':'example.dsc','url':'https://deb.debian.org/debian/pool/main/e/example/example.dsc','sha256':hashlib.sha256(desc).hexdigest(),'size':len(desc)}
    (tmp_path/descriptor['filename']).write_bytes(desc)
    manifest={'schema_version':1,'publication_cleared':False,'sources':[{'source':'example','version':'1:2.0-1','descriptor':descriptor,'archives':[archive]}]}
    base={'packages':[{'source':'example','source_version':'1:2.0-1'}]}
    return tmp_path,manifest,base

def test_complete_descriptor_bound_bundle(bundle):
    assert verify(*bundle)['source_files']==2

@pytest.mark.parametrize('change',['missing-source','extra-source','archive-bytes','descriptor-bytes','version','checksums','url','path'])
def test_changed_or_incomplete_bundle_refuses(bundle,change):
    root,m,b=bundle;s=m['sources'][0]
    if change=='missing-source':m['sources']=[]
    if change=='extra-source':m['sources'].append(s)
    if change=='archive-bytes':(root/'example.tar.xz').write_bytes(b'changed')
    if change=='descriptor-bytes':(root/'example.dsc').write_bytes(b'changed')
    if change=='version':b['packages'][0]['source_version']='2:0'
    if change=='checksums':
        p=root/'example.dsc';p.write_bytes(p.read_bytes().replace(b'Checksums-Sha256:',b'Other-Checksums:'));s['descriptor'].update(sha256=hashlib.sha256(p.read_bytes()).hexdigest(),size=p.stat().st_size)
    if change=='url':s['archives'][0]['url']='https://evil.invalid/file'
    if change=='path':s['archives'][0]['filename']='../outside'
    with pytest.raises(ValueError):verify(root,m,b)

def test_redirects_refused():
    with pytest.raises(ValueError):NoRedirects().redirect_request(None,None,302,'',{},'https://deb.debian.org/other')

from classify_debian_findings import classify

def test_classification_requires_exact_archive_path_bytes_and_coordinates(tmp_path):
    p=tmp_path/'test.tar.gz';p.write_bytes(b'fixture')
    m={'sources':[{'descriptor':{'filename':'test.tar.gz','sha256':hashlib.sha256(b'fixture').hexdigest()},'archives':[]}]}
    b={'members':[{'archive':'test.tar.gz','member':'nested.tar.xz!source/test.pem','rules':{'private-key':[[3,5]]}}]}
    f={'File':str(p)+'!nested.tar.xz!source/test.pem','RuleID':'private-key','StartLine':3,'EndLine':5}
    assert classify([f],tmp_path,m,b)==(1,[])
    for key,value in [('File',str(tmp_path/'runtime.pem')+'!nested.tar.xz!source/test.pem'),('StartLine',4),('EndLine',6),('RuleID','other')]:
        changed=dict(f,**{key:value});assert classify([changed],tmp_path,m,b)==(0,[changed])
    p.write_bytes(b'altered');assert classify([f],tmp_path,m,b)==(0,[f])


@pytest.mark.parametrize('url', [
    'https://snapshot.debian.org/archive/debian/latest/file',
    'https://snapshot.debian.org/file/not-a-hash',
    'https://snapshot.debian.org/file/' + 'a'*40 + '?redirect=other',
    'https://snapshot.debian.org.evil.invalid/file/' + 'a'*40,
])
def test_snapshot_access_is_content_addressed_only(bundle, url):
    _, manifest, base = bundle
    manifest['sources'][0]['descriptor']['url'] = url
    with pytest.raises(ValueError): records(manifest, base)


def test_official_snapshot_with_sha256_bytes_is_supported(bundle):
    root, manifest, base = bundle
    manifest['sources'][0]['descriptor']['url'] = 'https://snapshot.debian.org/file/' + 'a'*40
    assert verify(root, manifest, base)['source_files'] == 2


def test_reviewed_manifest_covers_current_core_base():
    root = Path(__file__).resolve().parents[1]
    manifest = json.loads((root / 'licenses/debian-sources.json').read_text())
    base = json.loads((root / 'licenses/base-image.json').read_text())
    assert len(records(manifest, base)) == 222
