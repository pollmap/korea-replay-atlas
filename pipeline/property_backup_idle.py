"""Reuse a verified private backup when checkpoint contents did not change.

Only the ephemeral collector lease is excluded. Raw descriptors, quotas,
planning, schema and scheduler tables remain part of the logical fingerprint.
An existing backup can bootstrap the cache by restoring its checkpoint alone.
It never deletes an archive, calls a source or promotes a public release.
"""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile

from .real_estate import RealEstateError, canonical_bytes, _reject_links
from .real_estate_local_archive_set import load_set
from .real_estate_manifest import read_file

VERSION = 'checkpoint-idle-backup-v1'
MAX_CHECKPOINT = 128 * 1024**2

def fingerprint(path, *, closed=False):
 path=Path(path).absolute();_reject_links(path)
 if not path.is_file() or path.stat().st_size>MAX_CHECKPOINT:raise RealEstateError('idle_checkpoint_limit')
 digest=hashlib.sha256();quote=lambda value:'"'+value.replace('"','""')+'"'
 with closing(sqlite3.connect(path.as_uri()+'?mode=ro'+('&immutable=1' if closed else ''),uri=True)) as db:
  db.execute('BEGIN')
  tables=db.execute("SELECT name,sql FROM sqlite_master WHERE type='table' AND name!='lease' ORDER BY name").fetchall()
  if not {'jobs','calls','snapshots','meta'}<={name for name,_ in tables}:raise RealEstateError('idle_checkpoint_schema')
  digest.update(VERSION.encode())
  for name,schema in tables:
   digest.update(canonical_bytes([name,schema]))
   info=db.execute('PRAGMA table_info('+quote(name)+')').fetchall()
   keys=[row[1] for row in sorted(info,key=lambda row:row[5]) if row[5]]
   order=','.join(map(quote,keys)) if keys else 'rowid'
   for row in db.execute('SELECT * FROM '+quote(name)+' ORDER BY '+order):digest.update(canonical_bytes(list(row)))
 return digest.hexdigest()

def _marker(root):return Path(root).parent/'backup-idle-state.json'

def remember(root,store,head):
 if (Path(root)/'checkpoint.sqlite').stat().st_size>MAX_CHECKPOINT:return False
 if store.head()!=head:raise RealEstateError('idle_backup_head_changed')
 value={'version':VERSION,'fingerprint':fingerprint(Path(root)/'checkpoint.sqlite'),'head':head}
 p=_marker(root);_reject_links(p);temporary=p.with_suffix('.next');_reject_links(temporary)
 temporary.write_bytes(canonical_bytes(value));temporary.replace(p)

def reuse(root,store):
 if (Path(root)/'checkpoint.sqlite').stat().st_size>MAX_CHECKPOINT:return None
 head=store.head()
 if head is None:return None
 current=fingerprint(Path(root)/'checkpoint.sqlite');p=_marker(root);_reject_links(p)
 marker=None
 if p.is_file() and p.stat().st_size<=4096:
  try:marker=json.loads(p.read_bytes())
  except (ValueError,UnicodeError):pass
 if isinstance(marker,dict) and marker.get('version')==VERSION and marker.get('head')==head and marker.get('fingerprint')==current:
  return {'status':'unchanged','head':head,'checkpoint_fingerprint':current,'raw_reparsed':0,'checkpoint_restored':False}
 manifest=load_set(store,head)
 row=next(row for row in manifest['files'] if row['path']=='checkpoint.sqlite')
 if row['bytes']>MAX_CHECKPOINT:return None
 # Hash-verified closed archive; immutable is used only for this fixed copy.
 with tempfile.TemporaryDirectory(prefix='property-backup-checkpoint-') as folder:
  checkpoint=Path(folder)/'checkpoint.sqlite';checkpoint.write_bytes(read_file(row,store.get))
  saved=fingerprint(checkpoint,closed=True)
 if saved!=current:return None
 remember(root,store,head)
 return {'status':'unchanged','head':head,'checkpoint_fingerprint':current,'raw_reparsed':0,'checkpoint_restored':True}
