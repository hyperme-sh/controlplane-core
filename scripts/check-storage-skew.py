"""Offline first-parent/current Storage codec contract with a file-provider double.

No sockets, deployed credentials or runtime services are used. Both source trees
use the identical locked dependency graph; a changed graph requires a separate
owner qualification instead of silently substituting current dependencies.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import uuid

DRIVER = r'''
import asyncio, hashlib, json, sys
from pathlib import Path
from uuid import UUID
sys.path.insert(0, sys.argv[1])
from api.domain.tenant_records import TenantRecords, RecordConflict
class Files:
    def path(self, key): return Path(sys.argv[2]) / hashlib.sha256(key.encode()).hexdigest()
    async def fetch(self, key):
        p=self.path(key)
        if not p.exists(): return None
        value=p.read_bytes(); return value, '"'+hashlib.sha256(value).hexdigest()+'"'
    async def replace(self,key,data,etag):
        current=await self.fetch(key)
        if (current[1] if current else None)!=etag: raise RecordConflict()
        self.path(key).write_bytes(data)
    async def remove(self,key,etag):
        current=await self.fetch(key)
        if not current or current[1]!=etag: raise RecordConflict()
        self.path(key).unlink()
async def main():
    store=TenantRecords(UUID(sys.argv[3]),Files()); action=sys.argv[4]
    if action=='get':
        record=await store.get('fixtures','canary_codec')
        print(json.dumps(None if record is None else {'data':record.data.decode(),'revision':record.revision}))
    elif action=='put':
        try: result=await store.put('fixtures','canary_codec',sys.argv[5].encode(),None if sys.argv[6]=='none' else sys.argv[6])
        except RecordConflict: print(json.dumps({'conflict':True}))
        else: print(json.dumps({'data':result.data.decode(),'revision':result.revision}))
    else:
        try: await store.delete('fixtures','canary_codec',sys.argv[5])
        except RecordConflict: print(json.dumps({'conflict':True}))
        else: print(json.dumps({'deleted':True}))
asyncio.run(main())
'''


def check(root, baseline):
    import re
    if not re.fullmatch(r'[0-9a-f]{40}', baseline): raise ValueError('Exact baseline revision required')
    def git(*args): return subprocess.check_output(['git', *args], cwd=root)
    for path in ('pyproject.toml', 'uv.lock'):
        if git('show', baseline+':'+path) != (root/path).read_bytes():
            raise ValueError('Dependency graph differs; separate baseline environment required')
    with tempfile.TemporaryDirectory(prefix='core-codec-contract-') as tmp:
        temp=Path(tmp); old=temp/'baseline'; old.mkdir(); provider=temp/'provider'; provider.mkdir()
        # Checkout only source bytes from the selected commit; never execute hooks.
        names=git('ls-tree','-r','--name-only',baseline,'src').decode().splitlines()
        for name in names:
            destination=old/name; destination.parent.mkdir(parents=True,exist_ok=True)
            destination.write_bytes(git('show',baseline+':'+name))
        driver=temp/'driver.py'; driver.write_text(DRIVER)
        tenant=str(uuid.uuid4())
        def run(source, action, *args):
            result=subprocess.check_output([sys.executable,'-I',str(driver),str(source/'src'),str(provider),tenant,action,*args],cwd=temp,timeout=30)
            return json.loads(result)
        first=run(old,'put','baseline','none')
        assert run(root,'get')==first
        second=run(root,'put','candidate',first['revision']); assert second['revision']!=first['revision']
        assert run(old,'get')==second
        assert run(old,'put','stale',first['revision'])=={'conflict':True}
        assert run(root,'delete',first['revision'])=={'conflict':True}
        third=run(old,'put','rollback',second['revision']); assert run(root,'get')==third
        assert run(old,'delete',third['revision'])=={'deleted':True}
        assert run(root,'get') is None and not list(provider.iterdir())
        print(json.dumps({'baseline':baseline,'candidate':git('rev-parse','HEAD').decode().strip(),
                          'lock_sha256':hashlib.sha256((root/'uv.lock').read_bytes()).hexdigest(),
                          'storage_codec_contract':'passed','provider':'file double'}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__); parser.add_argument('--baseline',required=True)
    check(Path(__file__).resolve().parents[1],parser.parse_args().baseline)
