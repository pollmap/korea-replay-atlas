"""Reuse source audits only for identical inputs and verified normalizer versions."""
import ast
import hashlib
import inspect
import json
from pathlib import Path
import sqlite3
import subprocess
from .real_estate import RealEstateError, canonical_bytes, sha256, _reject_links

def version():
    from .real_estate_publish import verify_snapshot
    base=Path(__file__).parent
    files={name:sha256((base/name).read_bytes()) for name in ('real_estate.py','real_estate_storage.py')}
    files['verify_snapshot']=sha256(ast.dump(ast.parse(inspect.getsource(verify_snapshot)),include_attributes=False).encode())
    return sha256(canonical_bytes({'schema_version':1,'sources':files}))

def job_hash(job):
    keys=('id','trade_type','lawd_code','deal_month','priority','status','error_code','pages','snapshot','updated_at')
    return sha256(json.dumps({k:job[k] for k in keys},sort_keys=True,separators=(',',':')).encode())

def identity(partition):
    result=hashlib.sha256()
    for row in partition['records']:result.update((row['id']+'\n').encode())
    return result.hexdigest()

class VerificationCache:
    def __init__(self, filename, root):
        filename=Path(filename).absolute();_reject_links(filename);filename.parent.mkdir(parents=True,exist_ok=True)
        self.root=Path(root).resolve();self.version=version();self.hits=0;self.misses=0
        self.db=sqlite3.connect(filename)
        self.db.execute('create table if not exists audited (root text, version text, job_id text, input_sha text, records integer, identity_sha text, primary key(root,version,job_id,input_sha))')
        self.db.commit()

    def verify(self, root, job, verifier):
        from .real_estate_publish import checked_read
        from .real_estate_storage import decode_snapshot
        if Path(root).resolve()!=self.root:raise RealEstateError('verification_cache_root')
        if job['status'] not in ('complete','empty') or not job['snapshot']:return verifier(root,job)
        digest=job_hash(job)
        prior=self.db.execute('select records,identity_sha from audited where root=? and version=? and job_id=? and input_sha=?',(str(self.root),self.version,job['id'],digest)).fetchone()
        if prior:
            descriptor=json.loads(job['snapshot'])
            partition=json.loads(decode_snapshot(checked_read(root,descriptor,128*1024**2),descriptor))
            if len(partition['records'])!=prior[0] or identity(partition)!=prior[1]:raise RealEstateError('verification_cache_snapshot_mismatch')
            self.hits+=1;return partition
        partition=verifier(root,job);self.misses+=1
        self.db.execute('insert into audited values (?,?,?,?,?,?)',(str(self.root),self.version,job['id'],digest,len(partition['records']),identity(partition)));self.db.commit()
        return partition

    def import_completed_audit(self, folder, source_commit):
        from .real_estate_publish import verify_snapshot
        folder=Path(folder).absolute();_reject_links(folder)
        status=json.loads((folder/'status.json').read_bytes())
        if status.get('state')!='passed' or status.get('verified_jobs')!=status.get('total_jobs'):raise RealEstateError('legacy_audit_incomplete')
        repo=Path(__file__).parent.parent
        # The launcher used this exact committed verifier and source code. Refuse
        # adoption if its implementation changed, even if every input hash matches.
        for name in ('real_estate.py','real_estate_storage.py'):
            old=subprocess.run(['git','show',source_commit+':pipeline/'+name],cwd=repo,check=True,capture_output=True).stdout
            if old!=(repo/'pipeline'/name).read_bytes():raise RealEstateError('legacy_audit_code_version')
        old=subprocess.run(['git','show',source_commit+':pipeline/real_estate_publish.py'],cwd=repo,check=True,capture_output=True).stdout
        tree=ast.parse(old);fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='verify_snapshot')
        current=ast.parse(inspect.getsource(verify_snapshot)).body[0]
        if ast.dump(fn,include_attributes=False)!=ast.dump(current,include_attributes=False):raise RealEstateError('legacy_audit_code_version')
        launcher=(folder/'audit.py').read_text()
        if "root=Path('/srv/services/korea-replay/shared/data/collector')" not in launcher or str(self.root)!='/srv/services/korea-replay/shared/data/collector':raise RealEstateError('legacy_audit_root')
        manifest=json.loads((folder/'checkpoint-manifest.json').read_bytes())
        if sha256((folder/'checkpoint.sqlite').read_bytes())!=manifest['sha256']:raise RealEstateError('legacy_audit_checkpoint_hash')
        source=sqlite3.connect((folder/'audit.sqlite').as_uri()+'?mode=ro',uri=True)
        rows=source.execute('select job_id,input_sha,records,identity_sha from verified').fetchall();source.close()
        if len(rows)!=status['verified_jobs']:raise RealEstateError('legacy_audit_count')
        self.db.executemany('insert or ignore into audited values (?,?,?,?,?,?)',[(str(self.root),self.version,*r) for r in rows]);self.db.commit()
        return len(rows)

    def close(self):self.db.close()
