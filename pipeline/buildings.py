from __future__ import annotations
import json
import math
import numpy as np
import pyarrow.parquet as pq
import shapely
from shapely.geometry import mapping
import overturemaps.core
from .core import LOCAL,RELEASE,REGIONS,TRANSFORM_VERSION,atomic_json,register_asset,digest,now
from .elevation import sample_heights
from .building_heights import estimates

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
    raw=extract(region)
    source_meta=json.loads(raw.with_suffix('.meta.json').read_text())
    features=[];counts={'source_height':0,'estimated_height':0,'grid_estimated_height':0,'unknown_height':0,'invalid_geometry':0,'already_published_pilot':0}
    height_failures={};height_retry_ids=[]
    existing=set()
    if region.startswith('kr-'):
        for pilot in ('daejeon','sejong','cheongju'):
            pilot_raw=LOCAL/'raw'/'overture'/RELEASE/f'{pilot}-building.parquet'
            if pilot_raw.exists():existing.update(pq.read_table(pilot_raw,columns=['id'])['id'].to_pylist())
    # Streaming extraction remains bounded; rendering is spatially partitioned below.
    for batch in pq.ParquetFile(raw).iter_batches(batch_size=5000):
        rows=batch.to_pylist()
        valid=[]
        for row in rows:
            if row['id'] in existing:
                counts['already_published_pilot']+=1;continue
            geom=shapely.from_wkb(row['geometry'])
            if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon','MultiPolygon'):
                counts['invalid_geometry']+=1;continue
            valid.append((row,geom))
        if not valid:continue
        points=[g.representative_point() for _,g in valid]
        base_heights=sample_heights([p.x for p in points],[p.y for p in points])
        # Source height / floors are usable even when optional GHSL is unavailable.
        missing=[i for i,(row,_) in enumerate(valid) if height_of(row)[0] is None]
        grid_heights=[(None,None)]*len(valid)
        if missing:
            values=estimates([points[i].x for i in missing],[points[i].y for i in missing],failures=height_failures)
            for i,value in zip(missing,values):grid_heights[i]=value
        for (row,geom),base_height,(grid_height,grid_hash) in zip(valid,base_heights,grid_heights):
            height,evidence=height_of(row)
            method='source' if height is not None and evidence=='source_attribute' else 'floors' if height is not None else 'unknown'
            if height is None and grid_height is not None:
                height,evidence,method=grid_height,'estimate','ghsl_cell_average_2018'
            elif height is None and grid_hash is None and height_failures:
                height_retry_ids.append(row['id'])
            counts['source_height' if method=='source' else 'estimated_height' if method=='floors' else 'grid_estimated_height' if method=='ghsl_cell_average_2018' else 'unknown_height']+=1
            provenance={'source_id':'overture','source_record_id':row['id'],'dataset_version':RELEASE,'observed_at':None,'retrieved_at':source_meta['retrieved_at'],'evidence_type':evidence,'input_hash':source_meta['sha256'],'transform_version':TRANSFORM_VERSION,'unit':'m','vertical_datum':'WGS84 ellipsoidal (terrain transformed from EGM96)'}
            description='2018년 GHSL 100m 격자 평균에서 추정한 표현 높이입니다. 개별 건물의 실측 높이가 아닙니다.' if method=='ghsl_cell_average_2018' else '층수 × 3m 추정 높이' if method=='floors' else '높이 자료 미확인' if height is None else '원천 건물 높이 속성'
            features.append({'type':'Feature','id':row['id'],'geometry':mapping(geom),'properties':{'name':(row.get('names') or {}).get('primary') or '건물','height':height,'height_method':method,'height_source':'ghsl' if method=='ghsl_cell_average_2018' else 'overture','height_source_hash':grid_hash if method=='ghsl_cell_average_2018' else source_meta['sha256'],'min_height':row.get('min_height') or 0,'base_height':float(base_height),'source_id':'overture','dataset_version':RELEASE,'evidence_type':evidence,'provenance':provenance,'description':description}})
        print(json.dumps({'stage':'normalize','region':region,'features':len(features),**counts}),flush=True)
    # GeoJSON is retained as an auditable interchange; 3D Tiles conversion follows.
    content_hash=__import__('hashlib').sha256(json.dumps(features,sort_keys=True).encode()).hexdigest()[:12]
    output=LOCAL/'silver'/'buildings'/RELEASE/f'{region}-{content_hash}.geojson'
    atomic_json(output,{'type':'FeatureCollection','features':features})
    atomic_json(LOCAL/'audit'/f'buildings-{region}.json',{'source':source_meta,'published_features':len(features),**counts})
    if height_failures or (LOCAL/'audit'/'building-height-retries'/f'{region}.json').exists():
        atomic_json(LOCAL/'audit'/'building-height-retries'/f'{region}.json',{
            'region':region,'source_hash':source_meta['sha256'],'retry_required':bool(height_retry_ids),
            'failed_tiles':height_failures,'source_record_ids':height_retry_ids,
            'notice':'Optional GHSL unavailable: affected buildings retain unknown height; no zero or invented height was substituted.',
            'updated_at':now()})
    register_asset({'id':f'normalized-buildings-{region}','format':'geojson','path':str(output),'bbox':list(REGIONS[region]),'sha256':digest(output),'count':len(features)},private=True)
    return output
