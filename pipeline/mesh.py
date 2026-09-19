"""Generate glTF 2.0 / 3D Tiles 1.1 with per-building evidence metadata."""
from __future__ import annotations
import hashlib
import json
import math
import struct
from collections import defaultdict
import mapbox_earcut
import numpy as np
from pyproj import Transformer
from shapely.geometry import shape
from .core import PUBLIC,REGIONS,RELEASE,atomic_json,publish_file

ECEF=Transformer.from_crs(4979,4978,always_xy=True)

def frame(lon,lat):
    lo,la=math.radians(lon),math.radians(lat)
    east=np.array([-math.sin(lo),math.cos(lo),0.])
    north=np.array([-math.sin(la)*math.cos(lo),-math.sin(la)*math.sin(lo),math.cos(la)])
    up=np.array([math.cos(la)*math.cos(lo),math.cos(la)*math.sin(lo),math.sin(la)])
    rotation=np.column_stack([east,north,up])
    origin=np.array(ECEF.transform(lon,lat,0))
    transform=np.eye(4);transform[:3,:3]=rotation;transform[:3,3]=origin
    return origin,rotation,transform.flatten(order='F').tolist()

def write_glb(features,path):
    bounds=np.array([shape(f['geometry']).bounds for f in features])
    west,south=bounds[:,:2].min(axis=0);east,north=bounds[:,2:].max(axis=0)
    origin,rotation,transform=frame((west+east)/2,(south+north)/2)
    vertices=[];normals=[];feature_ids=[];triangles=[];properties=[];vertex_count=0
    low=1e9;high=-1e9
    for feature in features:
        prop={**feature['properties'],'source_record_id':feature['properties'].get('source_record_id') or feature.get('id') or feature['properties'].get('provenance',{}).get('source_record_id','')};height=prop.get('height')
        if not height or height<=0:continue
        geom=shape(feature['geometry'])
        polygons=list(geom.geoms) if geom.geom_type=='MultiPolygon' else [geom]
        feature_id=len(properties);properties.append(prop)
        ground=prop['base_height'];bottom=ground+min(prop.get('min_height',0),height);top=ground+height
        low=min(low,bottom);high=max(high,top)
        for polygon in polygons:
            from shapely.geometry.polygon import orient
            polygon=orient(polygon,sign=1.)
            rings=[np.asarray(polygon.exterior.coords)[:-1,:2]]+[np.asarray(r.coords)[:-1,:2] for r in polygon.interiors]
            coords=np.concatenate(rings)
            x,y,z=ECEF.transform(coords[:,0],coords[:,1],np.full(len(coords),ground))
            local=(np.column_stack([x,y,z])-origin)@rotation
            planar=local[:,:2].astype(np.float64)
            roof_indices=mapbox_earcut.triangulate_float64(planar,np.cumsum([len(r) for r in rings],dtype=np.uint32)).reshape(-1,3)
            roof=np.column_stack([local[:,0],local[:,2]+height,-local[:,1]])
            offset=vertex_count;vertices.append(roof);normals.append(np.tile([0.,1.,0.],(len(roof),1)));feature_ids.append(np.full(len(roof),feature_id,dtype='<f4'));triangles.append(roof_indices+offset);vertex_count+=len(roof)
            ring_start=0
            for ring in rings:
                # Vectorize all walls in a ring; retain original vertices and
                # winding while avoiding millions of tiny Python cross products.
                a=np.arange(ring_start,ring_start+len(ring));b=np.roll(a,-1)
                pa=np.column_stack([local[a,0],local[a,2]+bottom-ground,-local[a,1]])
                pb=np.column_stack([local[b,0],local[b,2]+bottom-ground,-local[b,1]])
                pc=roof[b];pd=roof[a]
                n=np.cross(pb-pa,pc-pa);length=np.linalg.norm(n,axis=1);valid=length>0
                walls=np.stack([pa[valid],pb[valid],pc[valid],pd[valid]],axis=1).reshape(-1,3)
                count=int(valid.sum());offsets=vertex_count+np.arange(count)*4
                if count:
                    vertices.append(walls);normals.append(np.repeat(n[valid]/length[valid,None],4,axis=0));feature_ids.append(np.full(count*4,feature_id,dtype='<f4'))
                    triangles.append((offsets[:,None,None]+np.array([[0,1,2],[0,2,3]])).reshape(-1,3));vertex_count+=count*4
                ring_start+=len(ring)
    if not vertices:return None
    precision=max(float(np.max(np.abs(v-v.astype('<f4').astype(np.float64)))) for v in vertices)*math.sqrt(3)
    position=np.concatenate(vertices).astype('<f4');normal=np.concatenate(normals).astype('<f4');ids=np.concatenate(feature_ids).astype('<f4');indices=np.concatenate(triangles).astype('<u4').ravel()
    blob=bytearray();views=[];accessors=[]
    def view(data,target=None):
        # glTF bufferViews require a positive byteLength. Empty string columns
        # retain zero offsets and share this unused padding byte.
        if not len(data):data=b'\0'
        while len(blob)%4:blob.append(0)
        value={'buffer':0,'byteOffset':len(blob),'byteLength':len(data)}
        if target:value['target']=target
        views.append(value);blob.extend(data);return len(views)-1
    for data,component,kind,target in [(position,5126,'VEC3',34962),(normal,5126,'VEC3',34962),(ids,5126,'SCALAR',34962),(indices,5125,'SCALAR',34963)]:
        accessor={'bufferView':view(data.tobytes(),target),'componentType':component,'count':len(data),'type':kind}
        if kind=='VEC3':accessor.update({'min':data.min(axis=0).tolist(),'max':data.max(axis=0).tolist()})
        accessors.append(accessor)
    schema={};table={}
    for key in ['name','source_id','source_record_id','dataset_version','evidence_type','height_method','height_source','description','raw_height','input_height','render_height','render_min_height','height_semantics','quality_flags','quality_state','render_eligible','lod_role','quality_policy','raw_min_height','height_semantics_basis','upstream_record_id','ground_vertical_datum','ground_accuracy_verified','original_properties']:
        encoded=[(json.dumps(p[key],ensure_ascii=False,separators=(',',':')) if isinstance(p.get(key),(list,dict)) else str(p.get(key,'') if p.get(key) is not None else '')).encode('utf-8') for p in properties]
        offsets=np.asarray([0]+list(np.cumsum([len(v) for v in encoded])),dtype='<u4')
        schema[key]={'type':'STRING'};table[key]={'values':view(b''.join(encoded)),'stringOffsets':view(offsets.tobytes()),'stringOffsetType':'UINT32'}
    schema['height']={'type':'SCALAR','componentType':'FLOAT32'};table['height']={'values':view(np.asarray([p['height'] for p in properties],dtype='<f4').tobytes())}
    gltf={'asset':{'version':'2.0','generator':'KOREA REPLAY'},'scene':0,'scenes':[{'nodes':[0]}],'nodes':[{'mesh':0}],
      'meshes':[{'primitives':[{'attributes':{'POSITION':0,'NORMAL':1,'_FEATURE_ID_0':2},'indices':3,'material':0,'extensions':{'EXT_mesh_features':{'featureIds':[{'featureCount':len(properties),'attribute':0,'propertyTable':0}]}}}]}],
      'materials':[{'pbrMetallicRoughness':{'baseColorFactor':[.83,.82,.75,1],'metallicFactor':0,'roughnessFactor':.92},'doubleSided':True}],
      'buffers':[{'byteLength':len(blob)}],'bufferViews':views,'accessors':accessors,
      'extensionsUsed':['EXT_mesh_features','EXT_structural_metadata'],
      'extensions':{'EXT_structural_metadata':{'schema':{'id':'korea-replay-buildings','classes':{'building':{'properties':schema}}},'propertyTables':[{'class':'building','count':len(properties),'properties':table}]}}}
    header=json.dumps(gltf,separators=(',',':'),ensure_ascii=False).encode('utf-8');header+=b' '*((-len(header))%4)
    blob+=b'\0'*((-len(blob))%4)
    result=struct.pack('<4sII',b'glTF',2,12+8+len(header)+8+len(blob))+struct.pack('<I4s',len(header),b'JSON')+header+struct.pack('<I4s',len(blob),b'BIN\0')+blob
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(result)
    return {'boundingVolume':{'region':[math.radians(v) for v in [west,south,east,north]]+[low-1,high+1]},'geometricError':0,'transform':transform,'content':{'uri':path.name},'extras':{'coordinate_rounding_max_m':precision}},len(properties)

def tiles(region:str):
    import sqlite3
    from .core import LOCAL
    with sqlite3.connect(LOCAL/'catalog.sqlite') as db:
        row=db.execute('SELECT payload FROM assets WHERE id=?',(f'normalized-buildings-{region}',)).fetchone()
    if row is None:raise ValueError('Run buildings normalization first')
    from pathlib import Path
    asset=json.loads(row[0]);path=Path(asset['path'])
    features=json.loads(path.read_text(encoding='utf-8'))['features']
    unknown=[f for f in features if not f['properties'].get('height')]
    if unknown:
        footprint=PUBLIC/'buildings'/RELEASE/f'{region}-unknown-{asset["sha256"][:12]}.geojson'
        atomic_json(footprint,{'type':'FeatureCollection','features':unknown})
        publish_file(footprint,asset_id=f'unknown-buildings-{region}',layer='buildings',format='geojson',bbox=REGIONS[region],source_id='overture',version=RELEASE,count=len(unknown))
    groups=defaultdict(list)
    grid_size=.02 if region.startswith('kr-') else .005
    for f in features:
        if not f['properties'].get('height'):continue
        p=shape(f['geometry']).representative_point();groups[(math.floor(p.x/grid_size),math.floor(p.y/grid_size))].append(f)
    mesh_version='m3' if region.startswith('kr-') else 'm2'
    root=PUBLIC/'buildings'/RELEASE/f'{region}-tiles-{asset["sha256"][:12]}-{mesh_version}'
    children=[];count=0
    for i,(key,items) in enumerate(sorted(groups.items())):
        result=write_glb(items,root/f'{key[0]}-{key[1]}.glb')
        if result:children.append(result[0]);count+=result[1]
        if i%25==0:print(json.dumps({'stage':'3d-tiles','region':region,'tiles':i+1,'features':count}),flush=True)
    if not children:
        print(json.dumps({'stage':'3d-tiles-empty','region':region,'unknown':len(unknown)}),flush=True)
        return
    bounds=np.array([c['boundingVolume']['region'] for c in children])
    region_bounds=[float(bounds[:,0].min()),float(bounds[:,1].min()),float(bounds[:,2].max()),float(bounds[:,3].max()),float(bounds[:,4].min()),float(bounds[:,5].max())]
    tileset={'asset':{'version':'1.1'},'geometricError':1000,'root':{'boundingVolume':{'region':region_bounds},'geometricError':250,'refine':'REPLACE','children':children},'extras':{'source':'Overture Maps contributors; European Commission JRC GHS-BUILT-H R2023A','height_notice':'Heights from 2018 100m cell means are estimates, not individual building measurements.','source_sha256':asset['sha256']}}
    atomic_json(root/'tileset.json',tileset)
    publish_file(root/'tileset.json',asset_id=f'buildings-{region}',layer='buildings',format='3d-tiles',bbox=REGIONS[region],source_id='overture',version=RELEASE,count=count)
    print(json.dumps({'stage':'3d-tiles-complete','region':region,'tiles':len(children),'features':count}),flush=True)
