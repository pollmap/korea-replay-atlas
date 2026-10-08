from __future__ import annotations
import json
import math
import pyarrow.parquet as pq
import shapely
from shapely.geometry import mapping
import overturemaps.core
from .core import LOCAL,RELEASE,REGIONS,atomic_json,register_asset,digest,now

def extract(region:str,kind='building'):
    target=LOCAL/'raw'/'overture'/RELEASE/f'{region}-{kind}.parquet'
    if target.exists() and target.with_suffix('.meta.json').exists():return target
    target.parent.mkdir(parents=True,exist_ok=True)
    reader=overturemaps.core.record_batch_reader(kind,bbox=REGIONS[region],release=RELEASE,stac=True,connect_timeout=30,request_timeout=120)
    if reader is None:raise RuntimeError('Overture source did not return a reader')
    part=target.with_suffix('.part')
    count=0
    with pq.ParquetWriter(part,reader.schema,compression='zstd') as writer:
        for batch in reader:
            writer.write_batch(batch);count+=batch.num_rows
            print(json.dumps({'stage':'extract','region':region,'kind':kind,'rows':count}),flush=True)
    part.replace(target)
    atomic_json(target.with_suffix('.meta.json'),{'source_id':'overture','dataset_version':RELEASE,'bbox':REGIONS[region],'count':count,'sha256':digest(target),'retrieved_at':now()})
    return target

def height_of(row):
    height=row.get('height')
    if isinstance(height,(int,float)) and math.isfinite(height) and height>0:return float(height),'source_attribute'
    floors=row.get('num_floors')
    if isinstance(floors,(int,float)) and math.isfinite(floors) and floors>0:return float(floors)*3,'estimate'
    return None,'unverified'

def buildings(region:str):
    """Preserve source footprints without downloading retired 3D height data."""
    raw=extract(region)
    source_meta=json.loads(raw.with_suffix('.meta.json').read_text())
    features=[]
    counts={'source_height':0,'estimated_height':0,'unknown_height':0,'invalid_geometry':0,'already_published_pilot':0}
    existing=set()
    if region.startswith('kr-'):
        for pilot in ('daejeon','sejong','cheongju'):
            pilot_raw=LOCAL/'raw'/'overture'/RELEASE/f'{pilot}-building.parquet'
            if pilot_raw.exists():existing.update(pq.read_table(pilot_raw,columns=['id'])['id'].to_pylist())
    for batch in pq.ParquetFile(raw).iter_batches(batch_size=5000):
        for row in batch.to_pylist():
            if row['id'] in existing:
                counts['already_published_pilot']+=1
                continue
            geom=shapely.from_wkb(row['geometry'])
            if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon','MultiPolygon'):
                counts['invalid_geometry']+=1
                continue
            height,evidence=height_of(row)
            method='source' if evidence=='source_attribute' else 'floors' if height is not None else 'unknown'
            counts['source_height' if method=='source' else 'estimated_height' if method=='floors' else 'unknown_height']+=1
            provenance={'source_id':'overture','source_record_id':row['id'],'dataset_version':RELEASE,
                'observed_at':None,'retrieved_at':source_meta['retrieved_at'],'evidence_type':evidence,
                'input_hash':source_meta['sha256'],'transform_version':'building-footprints-2d-v1'}
            features.append({'type':'Feature','id':row['id'],'geometry':mapping(geom),'properties':{
                'source_record_id':row['id'],'name':(row.get('names') or {}).get('primary') or '건물',
                'height':height,'height_method':method,'height_source':'overture','height_source_hash':source_meta['sha256'],
                'source_height':row.get('height'),'num_floors':row.get('num_floors'),'min_height':row.get('min_height'),
                'source_id':'overture','dataset_version':RELEASE,'evidence_type':evidence,'provenance':provenance}})
        print(json.dumps({'stage':'normalize','region':region,'features':len(features),**counts}),flush=True)
    content_hash=__import__('hashlib').sha256(json.dumps(features,sort_keys=True).encode()).hexdigest()[:12]
    output=LOCAL/'silver'/'buildings'/RELEASE/f'{region}-{content_hash}.geojson'
    atomic_json(output,{'type':'FeatureCollection','features':features})
    atomic_json(LOCAL/'audit'/f'buildings-{region}.json',{'source':source_meta,'published_features':len(features),**counts})
    register_asset({'id':f'normalized-buildings-{region}','format':'geojson','path':str(output),
        'bbox':list(REGIONS[region]),'sha256':digest(output),'count':len(features)},private=True)
    return output
