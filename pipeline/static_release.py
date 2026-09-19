"""Build a dependency-closed, immutable Workers Static Assets release.

No account resources are created and no source files are removed. Deployment
uses the generated config only after local and remote preview verification.
"""
from __future__ import annotations
import argparse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
import re
from pathlib import Path
import shutil
from .core import ROOT, PUBLIC, LOCAL, atomic_json, digest, now
from .geometry_budget import verify_geometry_budgets
from .building_counts import building_count_contract
from .recovery_bundle import broker_deployment_contract, no_links, read_json, regular_file, safe_relative, tree_files, valid_identity, validate_config

MAX_FILES = 20_000
MAX_FILE_BYTES = 24 * 1024 * 1024
NOT_FOUND = '<!doctype html><html lang="ko"><meta charset="utf-8"><title>자료 없음</title><body>요청한 자료를 찾을 수 없습니다.</body></html>'


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def local_url(url, base=PUBLIC):
    if not isinstance(url, str) or not url.startswith('/data/') or any(c in url for c in ('?', '#', '\\', '%', ':')):
        raise ValueError('Invalid public data URL')
    if any(p.startswith('.') or p in ('private','raw') for p in url[6:].split('/')):
        raise ValueError('Private or ambiguous public data path')
    path = (base / url[6:]).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError('Missing or unsafe public asset: ' + url)
    return path


def immutable_json(path,value):
    if path.exists():
        if json.loads(path.read_bytes())!=value:
            raise ValueError('Immutable release collision: '+str(path))
        # Existing URLs may already be cached for a year. Even harmless JSON key
        # reordering changes their integrity hash and must never rewrite bytes.
        return
    atomic_json(path,json.loads(encoded(value)))


def dependencies(asset, base=PUBLIC):
    path = local_url(asset['url'], base)
    found = set()

    def tileset(manifest_path, stack):
        if manifest_path in stack:
            raise ValueError('Cyclic tileset dependency')
        stack = stack | {manifest_path}
        manifest = json.loads(manifest_path.read_bytes())

        def visit(tile):
            contents = ([tile['content']] if tile.get('content') else []) + tile.get('contents', [])
            for content in contents:
                uri = content.get('uri', content.get('url'))
                if not isinstance(uri, str) or any(c in uri for c in (':', '?', '#', '\\')):
                    raise ValueError('Invalid tileset dependency URI')
                child = (manifest_path.parent / uri).resolve()
                if not child.is_relative_to(base.resolve()) or not child.is_file():
                    raise ValueError('Missing or unsafe tileset dependency')
                metadata=content.get('extras',tile.get('extras',{}))
                if 'sha256' in metadata and digest(child)!=metadata['sha256']:
                    raise ValueError('Tileset dependency hash mismatch')
                if 'bytes' in metadata and child.stat().st_size!=metadata['bytes']:
                    raise ValueError('Tileset dependency size mismatch')
                found.add(child)
                if child.suffix == '.json':
                    tileset(child, stack)
            for child in tile.get('children', []):
                visit(child)

        visit(manifest['root'])

    found.add(path)
    if asset['format'] == '3d-tiles':
        tileset(path, set())
    elif asset['format'] == 'quantized-mesh':
        layer = json.loads(path.read_bytes())
        for level, ranges in enumerate(layer['available']):
            for bounds in ranges:
                for x in range(bounds['startX'], bounds['endX'] + 1):
                    for y in range(bounds['startY'], bounds['endY'] + 1):
                        child = path.parent / str(level) / str(x) / f'{y}.terrain'
                        if not child.is_file() or child.stat().st_size < 92:
                            raise ValueError('Missing terrain dependency')
                        found.add(child.resolve())
    elif asset['format'] == 'imagery':
        for frame in json.loads(path.read_bytes())['frames']:
            found.add(local_url(frame['url'], base))
    elif asset['format'] == 'search-index':
        search = json.loads(path.read_bytes())
        if search.get('schema_version') == 2:
            for entry in [*search['buckets'], *search['pages']]:
                shard=local_url(entry['url'], base)
                if shard.stat().st_size!=entry['byte_length'] or digest(shard)!=entry['sha256']:
                    raise ValueError('Search shard integrity mismatch')
                found.add(shard)
    return found


def transit_dependencies(reference, base=PUBLIC):
    if not isinstance(reference,dict) or not re.fullmatch(r'/data/live-transit/routes/[a-f0-9]{16,64}/manifest\.json',reference.get('url','')):
        raise ValueError('Invalid transit manifest URL')
    def verified(entry, limit):
        path=local_url(entry.get('url'),base)
        if not isinstance(entry.get('byte_length'),int) or not 0<entry['byte_length']<=limit or path.stat().st_size!=entry['byte_length'] or digest(path)!=entry.get('sha256'):
            raise ValueError('Transit catalog integrity mismatch')
        return path
    manifest=verified(reference,256*1024)
    value=json.loads(manifest.read_bytes());cities=value.get('cities')
    if value.get('schema_version')!=1 or not isinstance(cities,list) or len(cities)>512:
        raise ValueError('Invalid transit manifest')
    files={manifest};codes=set();prefix=reference['url'].removesuffix('manifest.json')
    for city in cities:
        code=city.get('city_code')
        if not isinstance(code,str) or not re.fullmatch(r'\d{1,8}',code) or code in codes or not isinstance(city.get('location_service_supported'),bool):
            raise ValueError('Invalid transit city')
        codes.add(code)
        if city.get('status')=='failed':
            if any(k in city for k in ('url','sha256','byte_length')) or city.get('route_count')!=0:raise ValueError('Failed transit city has a usable shard')
            continue
        if city.get('status')!='complete' or city.get('url')!=prefix+code+'.json':raise ValueError('Invalid transit city shard URL')
        shard=verified(city,2*1024*1024);data=json.loads(shard.read_bytes())
        routes=data.get('routes')
        if data.get('schema_version')!=1 or data.get('city_code')!=code or data.get('city_name')!=city.get('city_name') or not isinstance(routes,list) or len(routes)!=city.get('route_count') or len(routes)>12000:
            raise ValueError('Transit city route count mismatch')
        ids=set()
        for route in routes:
            rid=route.get('route_id')
            if not isinstance(rid,str) or not re.fullmatch(r'[A-Za-z0-9가-힣_-]{1,60}',rid) or route.get('city_code')!=code or route.get('id')!=f'tago-{code}-{rid.lower()}' or route['id'] in ids:
                raise ValueError('Invalid transit route identity')
            ids.add(route['id'])
        files.add(shard)
    return files


def build_catalog(input_path:Path, base=PUBLIC):
    original = json.loads(input_path.read_bytes())
    if original.get('schema_version') != 1 or not original.get('assets'):
        raise ValueError('Expected a complete flat v1 input catalog')
    assets = []
    for asset in original['assets']:
        building_count_contract(asset)
        if not isinstance(asset.get('version'),str) or not asset['version'].strip():
            raise ValueError('Missing source version: '+str(asset.get('id','unknown')))
        path = local_url(asset['url'], base)
        if digest(path) != asset['sha256']:
            raise ValueError('Asset hash mismatch: ' + asset['id'])
        assets.append({**asset, 'byte_length': path.stat().st_size})
    # Validate the input declarations before generated byte_length can mask an
    # incorrect or absent work budget. Publication must never use estimates.
    verify_geometry_budgets(original['assets'], base)
    assets.sort(key=lambda a:a['id'])
    if len({a['id'] for a in assets}) != len(assets):
        raise ValueError('Duplicate asset IDs')
    groups = defaultdict(list)
    core = []
    for asset in assets:
        if asset['format'] != 'geojson':
            core.append(asset)
            continue
        w,s,e,n = asset['bbox']
        detail = asset.get('detail_level', 'detail')
        # Nationwide overview geometry already has coarse coverage. Splitting its
        # tiny descriptors into geographic cells creates many serial HTTP waits
        # before first paint. Keep detailed geometry spatially indexed, and let
        # the existing 512-asset/read-byte limits bound merged overview indexes.
        cell = () if detail == 'overview' else (math.floor((w+e)), math.floor((s+n)))
        key = (asset['layer'], detail,
               asset.get('min_camera_height'), asset.get('max_camera_height'), *cell)
        groups[key].append(asset)
    indexes = []
    for key, group in sorted(groups.items(), key=lambda pair:str(pair[0])):
        # Keep a manifest small even where source geometry overlaps many cells.
        for at in range(0, len(group), 512):
            chunk = group[at:at+512]
            content = {'schema_version':1, 'assets':chunk}
            fingerprint = hashlib.sha256(encoded(content)).hexdigest()[:20]
            path = base / 'indexes' / f'{fingerprint}.json'
            immutable_json(path, content)
            if path.stat().st_size > 1024*1024:
                raise ValueError('Spatial index exceeds client read budget')
            descriptor = {'id':'index-'+fingerprint, 'layer':key[0], 'format':'asset-index',
                'url':'/data/'+path.relative_to(base).as_posix(), 'sha256':digest(path),
                'bbox':[min(a['bbox'][0] for a in chunk), min(a['bbox'][1] for a in chunk),
                        max(a['bbox'][2] for a in chunk), max(a['bbox'][3] for a in chunk)],
                'source_id':chunk[0]['source_id'], 'version':chunk[0]['version'],
                'count':sum(a.get('count',0) for a in chunk), 'byte_length':path.stat().st_size,
                'detail_level':key[1]}
            if key[2] is not None:descriptor['min_camera_height']=key[2]
            if key[3] is not None:descriptor['max_camera_height']=key[3]
            indexes.append(descriptor)
    coverage={'schema_version':1,'assets':[{k:a[k] for k in ['id','layer','format','bbox','from','to'] if k in a} for a in assets]}
    coverage_path=base/'indexes'/('coverage-'+hashlib.sha256(encoded(coverage)).hexdigest()[:20]+'.json')
    immutable_json(coverage_path,coverage)
    identity={**original,'assets':assets,'indexes':indexes};identity.pop('release_id',None)
    release = 'pub-'+hashlib.sha256(encoded(identity)).hexdigest()[:16]
    legacy = {**original, 'schema_version':1, 'release_id':release, 'assets':assets}
    legacy_path = base/'releases'/f'{release}.v1.json'
    immutable_json(legacy_path, legacy)
    current = {**legacy, 'schema_version':2, 'assets':core, 'indexes':indexes,
               'legacy_url':'/data/'+legacy_path.relative_to(base).as_posix(),
               'coverage_url':'/data/'+coverage_path.relative_to(base).as_posix()}
    path = base/'releases'/f'{release}.json'
    if path.exists() and json.loads(path.read_bytes()) != current:
        raise ValueError('Immutable release collision')
    immutable_json(path,current)
    return current, legacy, path, legacy_path


def prepare(input_path:Path, *, client_dir:Path|None=None, base=PUBLIC, output=LOCAL/'deploy'):
    base = base.resolve()
    current, legacy, release_path, legacy_path = build_catalog(input_path,base)
    files = {release_path,legacy_path,local_url(current['coverage_url'],base)}
    for asset in legacy['assets']:files.update(dependencies(asset,base))
    if current.get('live_transit_routes'):files.update(transit_dependencies(current['live_transit_routes'],base))
    for index in current['indexes']:files.add(local_url(index['url'],base))
    with ThreadPoolExecutor(max_workers=4) as pool:
        entries = list(pool.map(lambda p:{'path':str(p),'target':'data/'+p.relative_to(base.resolve()).as_posix(),
                            'bytes':p.stat().st_size,'sha256':digest(p)}, sorted(files)))
    hashes={e['target']:e['sha256'] for e in entries}
    for descriptor in [*legacy['assets'],*current['indexes']]:
        if hashes[descriptor['url'][1:]]!=descriptor['sha256']:
            raise ValueError('Asset changed during preparation: '+descriptor['id'])
    extras = []
    if client_dir:
        for path in sorted(client_dir.rglob('*')):
            if path.is_file():
                relative=path.relative_to(client_dir).as_posix()
                if relative.startswith('data/') or relative in ('_headers','404.html'):
                    raise ValueError('Client build contains a conflicting data namespace')
                extras.append({'path':str(path.resolve()),'target':relative,'bytes':path.stat().st_size,'sha256':digest(path)})
    all_entries = entries+extras
    if any(e['bytes']>=MAX_FILE_BYTES for e in all_entries):
        raise ValueError('Static file exceeds conservative 24 MiB ceiling')
    if len({e['target'] for e in all_entries}) != len(all_entries):raise ValueError('Duplicate output paths')
    # Count the pointer, headers manifest and 404 even though CF does not upload
    # _headers as an asset. The conservative local ceiling cannot undercount.
    if len(all_entries)+3>MAX_FILES:raise ValueError(f'Static file count {len(all_entries)+3} exceeds 20000')
    plan = {'schema_version':1,'mode':'static','release_id':current['release_id'],'created_at':now(),
        'catalog_path':str(release_path.resolve()),'catalog_hash':digest(release_path),
        'count':len(all_entries)+3,'bytes':sum(e['bytes'] for e in all_entries),
        'files':all_entries,'audit':{'passed':True,'release_id':current['release_id'],
                                  'scope':'Dependency closure, source hashes, static file count and sizes'}}
    atomic_json(output/'static-release-plan.json',plan)
    return plan


HEADERS = '''/data/*
  X-Content-Type-Options: nosniff
/data/catalog.json
  Cache-Control: no-cache
/download-gate.js
  Cache-Control: no-cache
/data/terrain/*.terrain
  Content-Type: application/vnd.quantized-mesh
/data/*.glb
  Content-Encoding: gzip
  Vary: Accept-Encoding
/assets/*
  Cache-Control: public, max-age=31536000, immutable
/*
  Referrer-Policy: strict-origin-when-cross-origin
'''


def headers_for(entries):
    # Keep GLBs as original raw bytes. Static Assets' automatic Response encoding
    # compresses them in transit; pre-gzipping the files here would double encode.
    # Verified on a nested-path Workers preview with gzip and identity clients.
    # Directory rules exclude catalog.json, avoiding combined no-cache and
    # immutable headers when more than one Cloudflare pattern matches.
    patterns=set()
    for entry in entries:
        parts=entry['target'].split('/')
        if parts[0]=='data':patterns.add('/'+('/'.join(parts[:2])+'/*' if len(parts)>2 else entry['target']))
    if len(patterns)+7>100:raise ValueError('Static header rules exceed Cloudflare limit')
    return HEADERS+''.join(f'{p}\n  Cache-Control: public, max-age=31536000, immutable\n' for p in sorted(patterns))


def _verified_file(path, size, sha, message):
    if regular_file(path).st_size != size or digest(path) != sha:
        raise ValueError(message + ': ' + str(path))


def _existing_file(path, size, sha):
    no_links(path)
    if not path.exists():return False
    _verified_file(path,size,sha,'Existing staged file is incomplete or changed')
    return True


def _copy_new(source, target, size, sha):
    # Exclusive creation matters: a staged target may share an inode with an
    # older immutable release, so it must never be opened in truncating mode.
    no_links(source);no_links(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    with source.open('rb') as incoming,target.open('xb') as outgoing:
        shutil.copyfileobj(incoming,outgoing,1024*1024)
    _verified_file(target,size,sha,'Source changed while copying')


def _write_new(target, content):
    sha=hashlib.sha256(content).hexdigest()
    if _existing_file(target,len(content),sha):return
    target.parent.mkdir(parents=True,exist_ok=True)
    with target.open('xb') as output:output.write(content)
    _verified_file(target,len(content),sha,'Generated metadata changed while staging')


def _json_bytes(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()


def _verified_reuse_bundle(path):
    """Validate a completed immutable bundle, never public or build inputs."""
    bundle=Path(path).absolute();no_links(bundle)
    receipt=read_json(bundle/'receipt.json');valid_identity(receipt)
    if (receipt.get('complete') is not True or receipt.get('mode')!='static'
        or receipt.get('audit',{}).get('passed') is not True
        or receipt.get('audit',{}).get('release_id')!=receipt['release_id']):
        raise ValueError('Reuse requires a complete audited staged bundle')
    if (Path(receipt.get('bundle','')).absolute()!=bundle
        or Path(receipt.get('config','')).absolute()!=bundle/'wrangler.json'):
        raise ValueError('Reuse receipt does not identify this staged bundle')
    for filename,key in [('asset-manifest.json','manifest_hash'),('wrangler.json','config_hash')]:
        regular_file(bundle/filename)
        if digest(bundle/filename)!=receipt.get(key):raise ValueError('Reuse bundle metadata hash mismatch: '+filename)
    validate_config(read_json(bundle/'wrangler.json'),receipt)
    entries=read_json(bundle/'asset-manifest.json')
    if (not isinstance(entries,list) or type(receipt.get('count')) is not int
        or not 1<=len(entries)==receipt['count']<=MAX_FILES):raise ValueError('Reuse asset count mismatch')
    names=set();assets={}
    for entry in entries:
        if not isinstance(entry,dict):raise ValueError('Invalid reuse asset entry')
        name=safe_relative(entry.get('target'))
        if (name.casefold() in names or type(entry.get('bytes')) is not int
            or not 0<=entry['bytes']<MAX_FILE_BYTES
            or not isinstance(entry.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',entry['sha256'])):
            raise ValueError('Invalid or duplicate reuse asset entry')
        names.add(name.casefold());assets[name]=entry
    actual={p.relative_to(bundle/'client').as_posix() for p in tree_files(bundle/'client')}
    if actual!=set(assets):raise ValueError('Reuse bundle has added or missing static files')
    workers=receipt.get('worker_files')
    if not isinstance(workers,list) or not 1<=len(workers)<=100:raise ValueError('Invalid reuse Worker files')
    worker_names=set()
    for entry in workers:
        if not isinstance(entry,dict):raise ValueError('Invalid reuse Worker entry')
        name=safe_relative(entry.get('target'))
        if not name.startswith('worker/') or name.casefold() in worker_names:
            raise ValueError('Invalid or duplicate reuse Worker target')
        worker_names.add(name.casefold())
        regular_file(bundle/name)
        if digest(bundle/name)!=entry.get('sha256'):raise ValueError('Reuse Worker hash mismatch')
    if {p.relative_to(bundle).as_posix().casefold() for p in tree_files(bundle/'worker')}!=worker_names:
        raise ValueError('Reuse bundle has added or missing Worker files')
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(lambda entry:_verified_file(bundle/'client'/entry['target'],entry['bytes'],entry['sha256'],'Reuse static file changed'),entries))
    if digest(bundle/'client/data/catalog.json')!=receipt.get('catalog_hash'):
        raise ValueError('Reuse catalog hash mismatch')
    catalog=read_json(bundle/'client/data/catalog.json')
    if catalog.get('schema_version')!=2 or catalog.get('release_id')!=receipt['release_id']:
        raise ValueError('Reuse catalog release mismatch')
    return bundle,assets,{'bundle_id':receipt['bundle_id'],'receipt_hash':digest(bundle/'receipt.json')}


def deployment_config(release_id):
    return {'name':'korea-replay','main':'./worker/index.js','compatibility_date':'2026-09-16',
        'compatibility_flags':['nodejs_compat'],'no_bundle':True,
        'assets':{'directory':'./client','binding':'ASSETS','not_found_handling':'none','run_worker_first':['/api/*']},
        'vars':{'ENVIRONMENT':'production','DATA_STORAGE':'static','COLLECTORS_ENABLED':'false',
                'STATIC_RELEASE_ID':release_id,'LIVE_TRANSIT_MODE':'broker'},
        'services':[broker_deployment_contract()['broker']],
        'version_metadata':{'binding':'CF_VERSION_METADATA'},'observability':{'enabled':True},
        'workers_dev':True,'preview_urls':True}


def stage(plan, *, output=LOCAL/'deploy', worker_dir=ROOT/'dist'/'korea_replay', reuse_bundle:Path|None=None):
    if not plan.get('audit',{}).get('passed') or plan['audit']['release_id']!=plan['release_id']:
        raise ValueError('An audited release plan is required')
    output=Path(output).absolute();worker_dir=Path(worker_dir).absolute()
    no_links(output);no_links(worker_dir)
    if not (worker_dir/'index.js').is_file():raise ValueError('Production Worker build is missing')
    if digest(Path(plan['catalog_path']))!=plan['catalog_hash']:raise ValueError('Catalog changed after preparation')
    contract=broker_deployment_contract()
    config=deployment_config(plan['release_id'])
    validate_config(config,{'schema_version':2,'release_id':plan['release_id'],'deployment_contract':contract})
    # Vite's Cloudflare plugin also emits .dev.vars and local deployment config.
    # Only compiled runtime artifacts belong in a portable, shareable release.
    worker_entries=[{'path':str(p),'target':'worker/'+p.relative_to(worker_dir).as_posix(),'sha256':digest(p)}
                    for p in sorted(worker_dir.rglob('*')) if p.is_file()
                    and p.suffix in ('.js','.mjs','.cjs','.wasm','.map')
                    and not any(part.startswith('.') for part in p.relative_to(worker_dir).parts)]
    headers=headers_for(plan['files'])
    fingerprint=hashlib.sha256(encoded({'assets':[(e['target'],e['sha256']) for e in plan['files']],
        'worker':[(e['target'],e['sha256']) for e in worker_entries],
        'catalog':plan['catalog_hash'],'headers':headers,'not_found':NOT_FOUND,'config_version':3,
        'config':config,'deployment_contract':contract})).hexdigest()[:16]
    destination=output/'bundles'/fingerprint
    client=destination/'client'
    reused,old_assets,reuse_info=_verified_reuse_bundle(reuse_bundle) if reuse_bundle is not None else (None,{},None)
    # Validate every target before creating anything. Generated names are reserved.
    target_names={'data/catalog.json','_headers','404.html'}
    for entry in plan['files']:
        name=safe_relative(entry['target'])
        if (name.casefold() in target_names or type(entry.get('bytes')) is not int
            or not 0<=entry['bytes']<MAX_FILE_BYTES
            or not isinstance(entry.get('sha256'),str) or not re.fullmatch('[a-f0-9]{64}',entry['sha256'])):
            raise ValueError('Invalid static staging entry')
        previous=old_assets.get(name)
        if name.startswith('data/') and previous and (previous['sha256']!=entry['sha256'] or previous['bytes']!=entry['bytes']):
            raise ValueError('Immutable public data URL changed across releases: '+name)
        target_names.add(name.casefold())
    if len(target_names)!=plan['count'] or len(target_names)>MAX_FILES:raise ValueError('Invalid final static file count')
    no_links(destination)
    client.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(client).free < plan['bytes']+30*1024**3:
        raise ValueError('Static staging would consume the 30 GiB disk reserve')
    def materialize(entry):
        target=client/entry['target'];source=Path(entry['path'])
        _verified_file(source,entry['bytes'],entry['sha256'],'Source changed after preparation')
        if _existing_file(target,entry['bytes'],entry['sha256']):return 'resumed',entry['bytes']
        previous=old_assets.get(entry['target'])
        if previous and previous['sha256']==entry['sha256'] and previous['bytes']==entry['bytes']:
            origin=reused/'client'/entry['target'];no_links(origin);no_links(target)
            target.parent.mkdir(parents=True,exist_ok=True)
            try:os.link(origin,target,follow_symlinks=False)
            except FileExistsError:raise ValueError('Staged target appeared while linking: '+str(target)) from None
            except OSError:
                # EXDEV, unsupported links or permissions: create a separate new
                # copy, preserving the old bundle and any partial target on error.
                _copy_new(source,target,entry['bytes'],entry['sha256'])
                return 'copied',entry['bytes']
            _verified_file(target,entry['bytes'],entry['sha256'],'Reused static file changed while linking')
            return 'linked',entry['bytes']
        _copy_new(source,target,entry['bytes'],entry['sha256'])
        return 'copied',entry['bytes']
    staging={kind:0 for kind in ('linked_files','linked_bytes','copied_files','copied_bytes','resumed_files','resumed_bytes')}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for index,(kind,size) in enumerate(pool.map(materialize,plan['files']),1):
            staging[kind+'_files']+=1;staging[kind+'_bytes']+=size
            if index%1000==0 or index==len(plan['files']):
                print(json.dumps({'stage':'static-stage','done':index,'total':len(plan['files']),**staging}),flush=True)
    catalog_source=Path(plan['catalog_path']);catalog_size=catalog_source.stat().st_size
    _verified_file(catalog_source,catalog_size,plan['catalog_hash'],'Catalog changed after preparation')
    if not _existing_file(client/'data/catalog.json',catalog_size,plan['catalog_hash']):
        _copy_new(catalog_source,client/'data/catalog.json',catalog_size,plan['catalog_hash'])
    _write_new(client/'_headers',headers.encode());_write_new(client/'404.html',NOT_FOUND.encode())
    actual={p.relative_to(client).as_posix() for p in tree_files(client)}
    if actual!={e['target'] for e in plan['files']}|{'data/catalog.json','_headers','404.html'}:
        raise ValueError('Final bundle has unexpected static files')
    for entry in worker_entries:
        target=destination/safe_relative(entry['target']);source=Path(entry['path']);size=source.stat().st_size
        _verified_file(source,size,entry['sha256'],'Worker changed while staging')
        if not _existing_file(target,size,entry['sha256']):_copy_new(source,target,size,entry['sha256'])
    if {p.relative_to(destination).as_posix() for p in tree_files(destination/'worker')}!={e['target'] for e in worker_entries}:
        raise ValueError('Final bundle has unexpected Worker files')
    _write_new(destination/'wrangler.json',_json_bytes(config))
    verification=[{'target':e['target'],'sha256':e['sha256'],'bytes':e['bytes']} for e in plan['files']]
    verification.extend({'target':name,'sha256':digest(client/name),'bytes':(client/name).stat().st_size}
                        for name in ['data/catalog.json','_headers','404.html'])
    _write_new(destination/'asset-manifest.json',_json_bytes(verification))
    receipt={**{k:v for k,v in plan.items() if k!='files'},'count':len(actual),'bundle':str(destination.resolve()),
             'config':str((destination/'wrangler.json').resolve()),'config_hash':digest(destination/'wrangler.json'),
             'manifest_hash':digest(destination/'asset-manifest.json'),'worker_files':worker_entries,
             'bundle_id':fingerprint,'complete':True,'bytes':sum(e['bytes'] for e in verification),
             'schema_version':2,'deployment_contract':contract,
             'staging':{**staging,**({'reuse_bundle':reuse_info} if reuse_info else {})}}
    archive=destination/'receipt.json'
    if archive.exists():
        prior=read_json(archive)
        if any(prior.get(key)!=receipt[key] for key in ('bundle_id','release_id','mode','bundle','config','count','bytes','catalog_hash','config_hash','manifest_hash','schema_version','deployment_contract')):
            raise ValueError('Archived bundle receipt does not match the staged release')
        if (prior.get('complete') is not True or prior.get('audit',{}).get('passed') is not True
            or prior.get('audit',{}).get('release_id')!=receipt['release_id']):
            raise ValueError('Archived bundle receipt is incomplete')
        expected_workers=[(entry['target'],entry['sha256']) for entry in worker_entries]
        if not isinstance(prior.get('worker_files'),list) or [(entry.get('target'),entry.get('sha256')) for entry in prior['worker_files']]!=expected_workers:
            raise ValueError('Archived bundle Worker receipt does not match the staged release')
        receipt=prior
    else:
        _write_new(archive,_json_bytes(receipt))
    atomic_json(output/'static-stage.json',receipt)
    return receipt


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--catalog',type=Path,default=LOCAL/'retile'/'catalog.json')
    parser.add_argument('--stage',action='store_true')
    parser.add_argument('--reuse-bundle',type=Path,help='Verified immutable staged bundle whose matching client files may be hardlinked')
    args=parser.parse_args()
    if args.reuse_bundle is not None and not args.stage:parser.error('--reuse-bundle requires --stage')
    plan=prepare(args.catalog,client_dir=ROOT/'dist'/'client' if args.stage else None)
    result=stage(plan,reuse_bundle=args.reuse_bundle) if args.stage else {k:v for k,v in plan.items() if k!='files'}
    print(json.dumps(result,ensure_ascii=False),flush=True)
