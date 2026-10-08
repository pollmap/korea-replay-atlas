"""Resumable 2D footprint partitioning from existing Overture source files."""
import json
import math
import shutil
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from .core import LOCAL, RELEASE, atomic_json, digest, download

def country_mask():
    path=download('https://download.geofabrik.de/asia/south-korea.poly',LOCAL/'raw'/'osm'/'south-korea.poly')
    lines=path.read_text().splitlines()[1:];rings=[];current=[];hole=False;holes=[]
    for line in lines:
        line=line.strip()
        if not line:continue
        if line=='END':
            if current:
                (holes if hole else rings).append(current);current=[]
            continue
        parts=line.split()
        if len(parts)==1:hole=line.startswith('!');continue
        current.append(tuple(map(float,parts)))
    mask=shapely.union_all([shapely.Polygon(r) for r in rings])
    if holes:mask=mask.difference(shapely.union_all([shapely.Polygon(r) for r in holes]))
    return mask,digest(path)


def partition():
    raw=LOCAL/'raw'/'overture'/RELEASE/'korea-building.parquet'
    if not raw.exists():raise ValueError('Run national building extraction first')
    meta=json.loads(raw.with_suffix('.meta.json').read_text(encoding='utf-8'))
    mask,mask_hash=country_mask();shapely.prepare(mask)
    root=LOCAL/'national'/f'{RELEASE}-{meta["sha256"][:12]}';root.mkdir(parents=True,exist_ok=True)
    state_path=root/'partition-state.json'
    state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {'next_batch':0,'input_rows':0,'accepted':0,'outside_mask':0,'invalid':0,'source_hash':meta['sha256'],'mask_hash':mask_hash}
    if state.get('mask_hash')!=mask_hash:raise ValueError('Country mask changed; create a new partition version')
    for batch_number,batch in enumerate(pq.ParquetFile(raw).iter_batches(batch_size=20000)):
        if batch_number<state['next_batch']:continue
        # Budget the current uncompressed batch, temporary writer output and checkpoint.
        required=max(batch.nbytes*3, 1024*1024)
        if shutil.disk_usage(root).free<required:
            raise RuntimeError(f'Footprint partition paused: current batch needs {required} bytes')
        geom=shapely.from_wkb(batch.column('geometry').to_pylist(),on_invalid='ignore')
        valid=shapely.is_valid(geom)&~shapely.is_empty(geom)
        points=shapely.point_on_surface(geom)
        inside=valid&shapely.covers(mask,points)
        x=shapely.get_x(points);y=shapely.get_y(points)
        groups={}
        for index in __import__('numpy').flatnonzero(inside):
            key=(math.floor(x[index]*10),math.floor(y[index]*10));groups.setdefault(key,[]).append(int(index))
        for (gx,gy),indices in groups.items():
            target=root/'cells'/f'{gx}-{gy}'/f'{batch_number:06d}.parquet';target.parent.mkdir(parents=True,exist_ok=True)
            temp=target.with_suffix('.part');pq.write_table(pa.Table.from_batches([batch.take(pa.array(indices))]),temp,compression='zstd');temp.replace(target)
        state.update({'next_batch':batch_number+1,'input_rows':state['input_rows']+batch.num_rows,'accepted':state['accepted']+int(inside.sum()),'outside_mask':state['outside_mask']+int((valid&~inside).sum()),'invalid':state['invalid']+int((~valid).sum())})
        atomic_json(state_path,state)
        if batch_number%20==0:print(json.dumps({'stage':'national-partition',**state}),flush=True)
    cells=[]
    for directory in sorted((root/'cells').iterdir()):
        gx,gy=map(int,directory.name.split('-'));name=f'kr-{gx}-{gy}'
        count=sum(pq.ParquetFile(p).metadata.num_rows for p in directory.glob('*.parquet'))
        cells.append({'name':name,'bbox':[gx/10,gy/10,(gx+1)/10,(gy+1)/10],'directory':str(directory),'count':count})
    manifest={'source':meta,'mask_hash':mask_hash,'cells':cells,'summary':state};atomic_json(root/'cells.json',manifest)
    atomic_json(LOCAL/'national'/'current.json',{'path':str(root/'cells.json')})
    print(json.dumps({'stage':'national-partition-complete','cells':len(cells),**state}),flush=True)
    return manifest
