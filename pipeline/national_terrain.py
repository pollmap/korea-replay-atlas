"""Resumable L12 land-cell terrain; independent of the building conversion job."""
from __future__ import annotations
import argparse
from concurrent.futures import ProcessPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import time
import numpy as np
from quantized_mesh_encoder import encode
from quantized_mesh_encoder.extensions import VertexNormalsExtension
from .core import LOCAL,PUBLIC,REGIONS,atomic_json,publish_file
from .elevation import sample_heights
from .terrain import tile_bounds,grid_indices,merge_terrain

def desired_tiles(cells,level):
    keys={(0,0,0),(0,1,0)}
    for cell in cells:
        w,s,e,n=cell['bbox']
        for z in range(1,level+1):
            width=180/2**z
            for x in range(math.floor((w+180)/width),math.floor((e+180)/width)+1):
                for y in range(math.floor((s+90)/width),math.floor((n+90)/width)+1):keys.add((z,x,y))
    return sorted(keys)

def generate_one(job):
    for attempt in range(12):
        try:return _generate_one(job)
        except PermissionError:
            if attempt==11:raise
            time.sleep(min(.1*2**attempt,2))

def _generate_one(job):
    root,key=job;level,x,y=key;output=Path(root)/str(level)/str(x)/f'{y}.terrain'
    if output.exists():return key
    if shutil.disk_usage(PUBLIC).free<30*1024**3:raise RuntimeError('Terrain paused: 30 GiB disk reserve')
    bounds=tile_bounds(level,x,y);size=33 if level<7 else 65
    lon,lat=np.meshgrid(np.linspace(bounds[0],bounds[2],size),np.linspace(bounds[1],bounds[3],size))
    heights=sample_heights(lon,np.clip(lat,-85.0511287,85.0511287),zoom=min(level+2,12))
    positions=np.column_stack([lon.ravel(),lat.ravel(),heights.ravel()]);indices=grid_indices(size)
    output.parent.mkdir(parents=True,exist_ok=True);temp=output.with_suffix(f'.{os.getpid()}.part')
    with temp.open('wb') as stream:encode(stream,positions,indices,bounds=bounds,extensions=[VertexNormalsExtension(indices=indices,positions=positions)])
    temp.replace(output);return key

def build(level=12,workers=3):
    if not 8<=level<=13 or not 1<=workers<=4:raise ValueError('Levels 8–13 and workers 1–4 are supported')
    pointer=json.loads((LOCAL/'national'/'current.json').read_text(encoding='utf-8'))
    cells=json.loads(Path(pointer['path']).read_text(encoding='utf-8'))['cells']
    keys=desired_tiles(cells,level)
    fingerprint=hashlib.sha256(json.dumps({'keys':keys,'encoder':'terrain-v1'}).encode()).hexdigest()[:16]
    root=PUBLIC/'terrain'/f'korea-land-l{level}-{fingerprint}';root.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(LOCAL/'catalog.sqlite',timeout=60) as db:
        sources=[json.loads(row[0]) for row in db.execute('SELECT payload FROM assets')]
    roots=[PUBLIC/a['url'].removeprefix('/data/').removesuffix('/layer.json') for a in sources if a.get('format')=='quantized-mesh']
    for z,x,y in keys:
        target=root/str(z)/str(x)/f'{y}.terrain'
        if target.exists():continue
        for source_root in roots:
            source=source_root/str(z)/str(x)/f'{y}.terrain'
            if source.exists():
                target.parent.mkdir(parents=True,exist_ok=True)
                try:os.link(source,target)
                except OSError:shutil.copy2(source,target)
                break
    remaining=[key for key in keys if not (root/str(key[0])/str(key[1])/f'{key[2]}.terrain').exists()]
    completed=len(keys)-len(remaining)
    print(json.dumps({'stage':'national-terrain-start','tiles':len(keys),'cached':completed,'remaining':len(remaining),'workers':workers}),flush=True)
    with ProcessPoolExecutor(max_workers=workers) as executor:
        for _ in executor.map(generate_one,((str(root),key) for key in remaining),chunksize=1):
            completed+=1
            if completed%100==0:
                report={'stage':'national-terrain','completed':completed,'total':len(keys)}
                atomic_json(LOCAL/'national'/'terrain-progress.json',report);print(json.dumps(report),flush=True)
    available=[[] for _ in range(level+1)]
    for z,x,y in keys:available[z].append({'startX':x,'endX':x,'startY':y,'endY':y})
    layer={'tilejson':'2.1.0','name':'KOREA REPLAY nationwide land detail','version':'1.0.0','format':'quantized-mesh-1.0','scheme':'tms','projection':'EPSG:4326','tiles':['{z}/{x}/{y}.terrain'],'extensions':['octvertexnormals'],'bounds':[-180,-90,180,90],'minzoom':0,'maxzoom':level,'available':available,'attribution':'Mapzen terrain / SRTM, GMTED, ETOPO; EGM96 to WGS84 ellipsoid via PROJ'}
    atomic_json(root/'layer.json',layer)
    publish_file(root/'layer.json',asset_id='terrain-korea-detail',layer='terrain',format='quantized-mesh',bbox=REGIONS['korea'],source_id='terrain',version=f'terrarium-egm96-{fingerprint}',count=len(keys))
    merge_terrain();atomic_json(LOCAL/'national'/'terrain-progress.json',{'stage':'complete','completed':len(keys),'total':len(keys)})

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--level',type=int,default=12);p.add_argument('--workers',type=int,default=3);a=p.parse_args();build(a.level,a.workers)
