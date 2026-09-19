import json
import hashlib
import struct
from .core import PUBLIC,LOCAL,digest,atomic_json,now

def audit(*, catalog_bytes=None, report_path=None):
    """Audit one supplied snapshot, or read the current pointer once for CLI use."""
    if catalog_bytes is None:
        catalog_bytes=(PUBLIC/'catalog.json').read_bytes()
    catalog=json.loads(catalog_bytes)
    catalog_hash=hashlib.sha256(catalog_bytes).hexdigest()
    results=[];errors=[]
    for asset in catalog['assets']:
        path=PUBLIC/asset['url'].removeprefix('/data/')
        if not path.is_file():errors.append(f'Missing asset: {asset["id"]}');continue
        if digest(path)!=asset['sha256']:errors.append(f'Hash mismatch: {asset["id"]}')
        checked=1
        if asset['format']=='3d-tiles':
            tileset=json.loads(path.read_text(encoding='utf-8'))
            for child in tileset['root']['children']:
                model=path.parent/child['content']['uri']
                if not model.exists():errors.append(f'Missing GLB: {model.name}');continue
                data=model.read_bytes()
                magic,version,length=struct.unpack('<4sII',data[:12])
                if magic!=b'glTF' or version!=2 or length!=len(data):errors.append(f'Invalid GLB envelope: {model.name}')
                checked+=1
        if asset['format']=='quantized-mesh':
            layer=json.loads(path.read_text(encoding='utf-8'))
            for level,rectangles in enumerate(layer['available']):
                for rect in rectangles:
                    for x in range(rect['startX'],rect['endX']+1):
                        for y in range(rect['startY'],rect['endY']+1):
                            tile=path.parent/str(level)/str(x)/f'{y}.terrain'
                            if not tile.exists() or tile.stat().st_size<92:errors.append(f'Missing/invalid terrain: {level}/{x}/{y}')
                            checked+=1
        results.append({'id':asset['id'],'format':asset['format'],'checked_files':checked,'records':asset['count']})
    report={'checked_at':now(),'release_id':catalog['release_id'],'catalog_hash':catalog_hash,'checks':results,'errors':errors,'passed':not errors,'scope':'File existence, manifest hash, GLB envelope and terrain availability only. Browser and source accuracy require separate verification.'}
    atomic_json(report_path or LOCAL/'audit'/'publication.json',report)
    if errors:raise ValueError(json.dumps(report,ensure_ascii=False))
    return report
