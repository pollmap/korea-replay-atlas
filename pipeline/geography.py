import json
from shapely.geometry import shape, mapping, box
from .core import LOCAL,PUBLIC,download,atomic_json,publish_file,now,digest

def country():
    raw=download('https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson',LOCAL/'raw'/'natural-earth'/'countries.geojson')
    features=json.loads(raw.read_text(encoding='utf-8'))['features']
    selected=[]
    for f in features:
        p=f['properties']
        if p.get('ADM0_A3')=='KOR' or p.get('SOV_A3')=='KOR':
            f['properties']={'name':'대한민국','source_id':'natural-earth','dataset_version':digest(raw)[:16],'evidence_type':'official_record','description':'전국 탐색용 소축척 경계입니다. 상세 해안선과 차이가 있습니다.'}
            selected.append(f)
    if not selected: raise ValueError('South Korea missing in source file')
    payload={'type':'FeatureCollection','features':selected}
    output=PUBLIC/'geography'/f'korea-{digest(raw)[:12]}.geojson'
    atomic_json(output,payload)
    publish_file(output,asset_id='country-outline',layer='terrain',format='geojson',bbox=(124.5,33,132,38.7),source_id='natural-earth',version=digest(raw)[:16],count=len(selected))
    print(json.dumps({'stage':'country','features':len(selected),'retrieved_at':now()},ensure_ascii=False),flush=True)
    return payload

def country_shape():
    from shapely.ops import unary_union
    files=list((PUBLIC/'geography').glob('korea-*.geojson'))
    data=json.loads(files[-1].read_text(encoding='utf-8')) if files else country()
    return unary_union([shape(f['geometry']) for f in data['features']])
