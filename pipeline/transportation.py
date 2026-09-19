import json
import shapely
import pyarrow.parquet as pq
from shapely.geometry import mapping
from .buildings import extract
from .core import PUBLIC,REGIONS,RELEASE,atomic_json,publish_file,digest

def transportation(region):
    raw=extract(region,'segment')
    groups={'road':[],'rail':[]}
    for batch in pq.ParquetFile(raw).iter_batches(batch_size=5000):
        for row in batch.to_pylist():
            category=row.get('subtype')
            if category not in groups:continue
            geom=shapely.from_wkb(row['geometry'])
            if geom.is_empty or not geom.is_valid:continue
            names=row.get('names') or {}
            groups[category].append({'type':'Feature','id':row['id'],'geometry':mapping(geom),'properties':{'name':names.get('primary') or ('철도' if category=='rail' else '도로'),'kind':category,'class':row.get('class'),'source_id':'overture-transportation','dataset_version':RELEASE,'evidence_type':'source_attribute','description':'공개 노선 형상의 지표 투영입니다. 교량·터널의 실측 높이는 포함하지 않습니다.'}})
    for category,features in groups.items():
        if not features:continue
        output=PUBLIC/'transportation'/RELEASE/f'{region}-{category}-{digest(raw)[:12]}.geojson'
        atomic_json(output,{'type':'FeatureCollection','features':features})
        publish_file(output,asset_id=f'{category}-{region}',layer='rail' if category=='rail' else 'terrain',format='geojson',bbox=REGIONS[region],source_id='overture-transportation',version=RELEASE,count=len(features))
    print(json.dumps({'stage':'transportation','region':region,'counts':{k:len(v) for k,v in groups.items()}}),flush=True)
