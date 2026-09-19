"""Dependency-closed, hashed R2 upload plan and conditional catalog promotion."""
from __future__ import annotations
import argparse
import hashlib
import json
import mimetypes
import os
import re
import tempfile
import time
from pathlib import Path
from .core import PUBLIC,LOCAL,atomic_json,digest,now

def asset_files(asset):
    path=(PUBLIC/asset['url'].removeprefix('/data/')).resolve()
    if not path.is_relative_to(PUBLIC.resolve()) or not path.is_file():raise ValueError('Invalid asset path')
    yield path
    if asset['format']=='3d-tiles':
        manifest=json.loads(path.read_text(encoding='utf-8'))
        def walk(tile):
            content=tile.get('content',{}).get('uri')
            if content:
                child=(path.parent/content).resolve()
                if not child.is_relative_to(PUBLIC.resolve()) or not child.is_file():raise ValueError('Missing tile dependency')
                yield child
            for child in tile.get('children',[]):yield from walk(child)
        yield from walk(manifest['root'])
    elif asset['format']=='quantized-mesh':
        layer=json.loads(path.read_text(encoding='utf-8'))
        for level,ranges in enumerate(layer['available']):
            for r in ranges:
                for x in range(r['startX'],r['endX']+1):
                    for y in range(r['startY'],r['endY']+1):yield path.parent/str(level)/str(x)/f'{y}.terrain'
    elif asset['format']=='imagery':
        manifest=json.loads(path.read_text(encoding='utf-8'))
        for frame in manifest['frames']:
            image=(PUBLIC/frame['url'].removeprefix('/data/')).resolve()
            if not image.is_relative_to(PUBLIC.resolve()):raise ValueError('Invalid imagery dependency')
            yield image

def release_snapshot():
    """Select the pointer once; all subsequent work uses immutable release bytes."""
    pointer=json.loads((PUBLIC/'catalog.json').read_bytes())
    release=pointer.get('release_id')
    if not isinstance(release,str) or not re.fullmatch(r'pub-[a-f0-9]{16}',release):
        raise ValueError('Invalid local release ID')
    path=(PUBLIC/'releases'/f'{release}.json').resolve()
    # core.register_asset currently replaces the pointer just before writing the
    # immutable release. Wait only for the selected ID, never switch snapshots.
    for attempt in range(21):
        try:
            content=path.read_bytes()
            break
        except (FileNotFoundError,PermissionError):
            if attempt==20:
                raise ValueError('Selected immutable release is not readable: '+release) from None
            time.sleep(.1)
    catalog=json.loads(content)
    if catalog.get('release_id')!=release:
        raise ValueError('Immutable release ID mismatch')
    if not isinstance(catalog.get('assets'),list):
        raise ValueError('Immutable release has no asset list')
    return path,content,catalog


def prepare():
    from .audit import audit
    release_path,catalog_bytes,catalog=release_snapshot()
    catalog_hash=hashlib.sha256(catalog_bytes).hexdigest()
    audit_path=LOCAL/'audit'/f'publication-{catalog["release_id"]}.json'
    report=audit(catalog_bytes=catalog_bytes,report_path=audit_path)
    files=set()
    expected_hashes={release_path:catalog_hash}
    for asset in catalog['assets']:
        files.update(asset_files(asset))
        asset_path=(PUBLIC/asset['url'].removeprefix('/data/')).resolve()
        if asset_path in expected_hashes and expected_hashes[asset_path]!=asset['sha256']:
            raise ValueError('Conflicting asset hashes in selected release')
        expected_hashes[asset_path]=asset['sha256']
    files.add(release_path)
    entries=[]
    for path in sorted(files):
        kind='application/vnd.quantized-mesh' if path.suffix=='.terrain' else 'model/gltf-binary' if path.suffix=='.glb' else 'application/geo+json' if path.suffix=='.geojson' else mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        sha256=digest(path)
        if path in expected_hashes and sha256!=expected_hashes[path]:
            raise ValueError('Selected immutable release or asset changed during preparation: '+path.name)
        entries.append({'key':'public/'+path.relative_to(PUBLIC.resolve()).as_posix(),'path':str(path),'bytes':path.stat().st_size,'sha256':sha256,'content_type':kind})
    result={'release_id':catalog['release_id'],'created_at':now(),'catalog_hash':catalog_hash,
            'catalog_path':str(release_path),'audit_path':str(audit_path),
            'audit':{'release_id':report['release_id'],'catalog_hash':report['catalog_hash'],'passed':report['passed']},
            'files':entries,'bytes':sum(e['bytes'] for e in entries),'count':len(entries)}
    atomic_json(LOCAL/'deploy'/'upload-plan.json',result)
    return result

def client():
    import boto3
    required=['CLOUDFLARE_ACCOUNT_ID','R2_ACCESS_KEY_ID','R2_SECRET_ACCESS_KEY','R2_BUCKET']
    missing=[key for key in required if not os.getenv(key)]
    if missing:raise ValueError('Missing environment variable names: '+', '.join(missing))
    account=os.environ['CLOUDFLARE_ACCOUNT_ID']
    if not __import__('re').fullmatch(r'[a-f0-9]{32}',account):raise ValueError('Invalid Cloudflare account ID')
    return boto3.client('s3',endpoint_url=f'https://{account}.r2.cloudflarestorage.com',aws_access_key_id=os.environ['R2_ACCESS_KEY_ID'],aws_secret_access_key=os.environ['R2_SECRET_ACCESS_KEY'],region_name='auto'),os.environ['R2_BUCKET']

def receipt_key(release):
    return f'private/publication/{release}.staged.json'


def canonical_bytes(value):
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def staging_receipt(plan,bucket):
    """Bind the audited snapshot and complete dependency plan, without local paths."""
    files=sorted(({k:e[k] for k in ('key','bytes','sha256')} for e in plan['files']),key=lambda e:e['key'])
    manifest={'schema_version':1,'release_id':plan['release_id'],'bucket':bucket,
              'catalog_hash':plan['catalog_hash'],'audit':plan['audit'],
              'count':len(files),'bytes':sum(e['bytes'] for e in files),'files':files}
    return {'manifest':manifest,'manifest_sha256':hashlib.sha256(canonical_bytes(manifest)).hexdigest(),
            'state':'complete'}


def validate_receipt(receipt,release,bucket):
    """Fail closed: an interrupted checkpoint is never a staging receipt."""
    try:
        manifest=receipt['manifest'];files=manifest['files']
        valid=(receipt['state']=='complete' and manifest['schema_version']==1
               and manifest['release_id']==release and manifest['bucket']==bucket
               and re.fullmatch(r'[a-f0-9]{64}',manifest['catalog_hash'])
               and receipt['manifest_sha256']==hashlib.sha256(canonical_bytes(manifest)).hexdigest()
               and manifest['audit']=={'release_id':release,'catalog_hash':manifest['catalog_hash'],'passed':True}
               and isinstance(files,list) and manifest['count']==len(files) and len(files)>0)
        if not valid:raise ValueError
        keys=[]
        for entry in files:
            key=entry['key'];keys.append(key)
            if (not isinstance(key,str) or not key.startswith('public/') or key=='public/catalog.json'
                or any(part in ('','.','..') for part in key.split('/')) or '\\' in key
                or type(entry['bytes']) is not int or entry['bytes']<0
                or not re.fullmatch(r'[a-f0-9]{64}',entry['sha256'])):raise ValueError
        if keys!=sorted(set(keys)) or manifest['bytes']!=sum(e['bytes'] for e in files):raise ValueError
        version=next(e for e in files if e['key']==f'public/releases/{release}.json')
        if version['sha256']!=manifest['catalog_hash']:raise ValueError
    except (KeyError,TypeError,ValueError,StopIteration):
        raise ValueError('Invalid staging receipt: release, bucket, audit, hash or dependency count mismatch') from None
    return manifest


def verify_remote_file(s3,bucket,entry):
    from botocore.exceptions import ClientError
    try:
        head=s3.head_object(Bucket=bucket,Key=entry['key'])
    except ClientError as error:
        if error.response['Error']['Code'] in ('404','NoSuchKey','NotFound'):
            raise ValueError('Staged dependency missing: '+entry['key']) from None
        raise
    if head['ContentLength']!=entry['bytes'] or head.get('Metadata',{}).get('sha256')!=entry['sha256']:
        raise ValueError('Staged dependency hash or size mismatch: '+entry['key'])


def upload():
    from botocore.exceptions import ClientError
    plan=prepare();s3,bucket=client()
    checkpoint=LOCAL/'deploy'/f'{plan["release_id"]}.json'
    receipt=staging_receipt(plan,bucket)
    validate_receipt(receipt,plan['release_id'],bucket)
    completed=[]
    for entry in plan['files']:
        try:
            head=s3.head_object(Bucket=bucket,Key=entry['key'])
            if head.get('Metadata',{}).get('sha256')==entry['sha256'] and head['ContentLength']==entry['bytes']:
                completed.append(entry['key']);continue
            raise ValueError('Immutable remote key already contains different content: '+entry['key'])
        except ClientError as error:
            if error.response['Error']['Code'] not in ('404','NoSuchKey','NotFound'):raise
        # Snapshot one file on disk, not in RAM, so a concurrent local writer
        # cannot change the transfer bytes while retaining the prepared hash.
        with tempfile.TemporaryFile() as body:
            sha=hashlib.sha256();size=0
            with Path(entry['path']).open('rb') as source:
                while chunk:=source.read(1024*1024):
                    body.write(chunk);sha.update(chunk);size+=len(chunk)
            if sha.hexdigest()!=entry['sha256'] or size!=entry['bytes']:
                raise ValueError('Local dependency changed since preparation: '+entry['key'])
            body.seek(0)
            try:
                s3.put_object(Bucket=bucket,Key=entry['key'],Body=body,ContentType=entry['content_type'],CacheControl='public,max-age=31536000,immutable',Metadata={'sha256':entry['sha256']},IfNoneMatch='*')
            except ClientError as error:
                if error.response['Error']['Code'] not in ('412','PreconditionFailed'):raise
                # A concurrent uploader may have won the same immutable key.
        verify_remote_file(s3,bucket,entry)
        completed.append(entry['key']);atomic_json(checkpoint,{'release_id':plan['release_id'],'uploaded':completed,'total':plan['count'],'checked_at':now()})
    # A checkpoint is progress only. Write the immutable receipt last, after
    # fresh verification of every dependency, including previously skipped files.
    for entry in plan['files']:verify_remote_file(s3,bucket,entry)
    content=canonical_bytes(receipt);key=receipt_key(plan['release_id'])
    try:
        s3.put_object(Bucket=bucket,Key=key,Body=content,ContentType='application/json',
                      CacheControl='no-store',IfNoneMatch='*')
    except ClientError as error:
        if error.response['Error']['Code'] not in ('412','PreconditionFailed'):raise
    if s3.get_object(Bucket=bucket,Key=key)['Body'].read()!=content:
        raise ValueError('Existing staging receipt conflicts with this dependency plan')
    atomic_json(LOCAL/'deploy'/f'{plan["release_id"]}.staged.json',receipt)
    print(json.dumps({'stage':'r2-staged','release_id':plan['release_id'],'files':len(completed),'manifest_sha256':receipt['manifest_sha256']}))

def promote(release):
    from botocore.exceptions import ClientError
    if not __import__('re').fullmatch(r'pub-[a-f0-9]{16}',release):raise ValueError('Invalid release')
    s3,bucket=client()
    try:
        receipt=json.loads(s3.get_object(Bucket=bucket,Key=receipt_key(release))['Body'].read())
    except ClientError as error:
        if error.response['Error']['Code'] in ('404','NoSuchKey','NotFound'):
            raise ValueError('Missing complete staging receipt; finish upload before promotion or rollback: '+release) from None
        raise
    manifest=validate_receipt(receipt,release,bucket)
    version=s3.get_object(Bucket=bucket,Key=f'public/releases/{release}.json')
    body=version['Body'].read();catalog=json.loads(body)
    if catalog['release_id']!=release or hashlib.sha256(body).hexdigest()!=manifest['catalog_hash']:
        raise ValueError('Staged release content hash mismatch')
    entries={entry['key']:entry for entry in manifest['files']}
    for asset in catalog['assets']:
        entry=entries.get('public/'+asset['url'].removeprefix('/data/'))
        if entry is None or entry['sha256']!=asset['sha256']:
            raise ValueError('Staging receipt does not cover catalog asset: '+asset['id'])
    # Rollback uses the target's remote receipt, never the local current catalog.
    # Objects must stay immutable: there is no store-wide transaction to guard
    # against an administrator deleting files after these checks.
    for entry in manifest['files']:verify_remote_file(s3,bucket,entry)
    # Read remote every time; a collector may have published since staging.
    expected={};previous=None
    try:
        current=s3.get_object(Bucket=bucket,Key='public/catalog.json')
        expected={'IfMatch':current['ETag']};previous=json.loads(current['Body'].read())['release_id']
    except ClientError as error:
        if error.response['Error']['Code'] not in ('404','NoSuchKey','NotFound'):raise
        expected={'IfNoneMatch':'*'}
    atomic_json(LOCAL/'deploy'/f'promotion-{__import__("time").time_ns()}.json',{'previous_release_id':previous,'release_id':release,'attempted_at':now()})
    s3.put_object(Bucket=bucket,Key='public/catalog.json',Body=body,ContentType='application/json',CacheControl='public,max-age=30',**expected)
    remote=s3.get_object(Bucket=bucket,Key='public/catalog.json')['Body'].read()
    if remote!=body:raise ValueError('Public manifest read-back mismatch')
    print(json.dumps({'stage':'r2-promoted','release_id':release,'previous_release_id':previous}))

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['prepare','upload','promote']);p.add_argument('--release');a=p.parse_args()
    if a.command=='prepare':
        plan=prepare();print(json.dumps({k:v for k,v in plan.items() if k!='files'},ensure_ascii=False,indent=2))
    elif a.command=='upload':upload()
    elif a.release:promote(a.release)
    else:p.error('--release is required for promotion and rollback')
if __name__=='__main__':main()
