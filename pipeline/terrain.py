from __future__ import annotations
import json
import math
import numpy as np
from quantized_mesh_encoder import encode
from quantized_mesh_encoder.extensions import VertexNormalsExtension
from .core import PUBLIC,REGIONS,atomic_json,publish_file
from .elevation import sample_heights

def tile_bounds(level:int,x:int,y:int):
    width=180/(2**level)
    return (-180+x*width,-90+y*width,-180+(x+1)*width,-90+(y+1)*width)

def grid_indices(size:int):
    rows,cols=np.mgrid[0:size-1,0:size-1]
    a=(rows*size+cols).ravel();b=a+1;c=a+size;d=c+1
    return np.stack([np.stack([a,b,c],axis=1),np.stack([b,d,c],axis=1)],axis=1).reshape(-1,3).astype(np.uint32)

def terrain(region:str,max_level:int):
    if not 0<=max_level<=13:raise ValueError('Terrain level must be between 0 and 13')
    region_bounds=REGIONS[region]
    root=PUBLIC/'terrain'/f'{region}-l{max_level}-v1'
    availability=[];total=0
    for level in range(max_level+1):
        width=180/(2**level)
        if level==0:sx,sy,ex,ey=0,0,1,0
        else:
            sx=math.floor((region_bounds[0]+180)/width);ex=math.floor((region_bounds[2]+180)/width)
            sy=math.floor((region_bounds[1]+90)/width);ey=math.floor((region_bounds[3]+90)/width)
        availability.append([{'startX':sx,'startY':sy,'endX':ex,'endY':ey}])
        for x in range(sx,ex+1):
            for y in range(sy,ey+1):
                output=root/str(level)/str(x)/f'{y}.terrain'
                if output.exists():total+=1;continue
                bounds=tile_bounds(level,x,y)
                size=33 if level<7 else 65
                lon,lat=np.meshgrid(np.linspace(bounds[0],bounds[2],size),np.linspace(bounds[1],bounds[3],size))
                heights=sample_heights(lon,np.clip(lat,-85.0511287,85.0511287),zoom=min(level+2,12))
                positions=np.column_stack([lon.ravel(),lat.ravel(),heights.ravel()])
                indices=grid_indices(size)
                output.parent.mkdir(parents=True,exist_ok=True)
                temp=output.with_suffix('.part')
                with temp.open('wb') as f:encode(f,positions,indices,bounds=bounds,extensions=[VertexNormalsExtension(indices=indices,positions=positions)])
                temp.replace(output);total+=1
        print(json.dumps({'stage':'terrain','region':region,'level':level,'tiles':total}),flush=True)
    layer={'tilejson':'2.1.0','name':f'KOREA REPLAY {region}','version':'1.0.0','format':'quantized-mesh-1.0','scheme':'tms','projection':'EPSG:4326','tiles':['{z}/{x}/{y}.terrain'],'extensions':['octvertexnormals'],'bounds':[-180,-90,180,90],'minzoom':0,'maxzoom':max_level,'available':availability,'attribution':'Mapzen terrain tiles / SRTM, GMTED, ETOPO; vertical datum EGM96 → WGS84 ellipsoid via PROJ'}
    atomic_json(root/'layer.json',layer)
    publish_file(root/'layer.json',asset_id=f'terrain-{region}',layer='terrain',format='quantized-mesh',bbox=region_bounds,source_id='terrain',version=f'terrarium-egm96-l{max_level}-v1',count=total)
    merge_terrain()


def merge_terrain():
    """One provider combines nationwide context and local refinements without races."""
    import hashlib
    import os
    import shutil
    import sqlite3
    from .core import LOCAL,digest
    with sqlite3.connect(LOCAL/'catalog.sqlite',timeout=60) as db:
        assets=[json.loads(row[0]) for row in db.execute('SELECT payload FROM assets ORDER BY id')]
    inputs=[a for a in assets if a.get('format')=='quantized-mesh' and a['id']!='terrain-combined']
    if not inputs:return
    content={}; origins=[]
    for asset in inputs:
        manifest=PUBLIC/asset['url'].removeprefix('/data/')
        layer=json.loads(manifest.read_text(encoding='utf-8'));origins.append(asset['sha256'])
        for level,rectangles in enumerate(layer['available']):
            for r in rectangles:
                for x in range(r['startX'],r['endX']+1):
                    for y in range(r['startY'],r['endY']+1):
                        key=(level,x,y);tile=manifest.parent/str(level)/str(x)/f'{y}.terrain'
                        if key in content and digest(tile)!=digest(content[key]):
                            raise ValueError(f'Terrain input conflict: {level}/{x}/{y}')
                        content[key]=tile
    fingerprint=hashlib.sha256(json.dumps(sorted(origins)).encode()).hexdigest()[:16]
    root=PUBLIC/'terrain'/f'merged-{fingerprint}'
    available=[[] for _ in range(max(k[0] for k in content)+1)]
    for (level,x,y),source in sorted(content.items()):
        target=root/str(level)/str(x)/f'{y}.terrain'
        if not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True)
            try:os.link(source,target)
            except OSError:shutil.copy2(source,target)
        available[level].append({'startX':x,'endX':x,'startY':y,'endY':y})
    layer.update({'name':'KOREA REPLAY national terrain with regional detail','available':available,'maxzoom':len(available)-1})
    atomic_json(root/'layer.json',layer)
    publish_file(root/'layer.json',asset_id='terrain-combined',layer='terrain',format='quantized-mesh',bbox=REGIONS['korea'],source_id='terrain',version=f'terrarium-egm96-{fingerprint}',count=len(content))
    print(json.dumps({'stage':'terrain-merged','inputs':len(inputs),'tiles':len(content)}),flush=True)
