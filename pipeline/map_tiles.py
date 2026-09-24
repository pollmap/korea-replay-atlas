"""Deterministic offline MVT/PMTiles publication. Never changes public pointers.

Original features remain in their source files. A resumable SQLite work index
contains projected geometry plus lossless selection properties, not raw copies.
Every original record remains addressable through immutable detail shards.
"""
from __future__ import annotations
import argparse
from collections import Counter
import gc
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import shutil
import sqlite3
import time
import zlib

import mapbox_vector_tile
from mapbox_vector_tile.Mapbox import vector_tile_pb2
import numpy as np
from pmtiles.tile import Compression,TileType,Entry,zxy_to_tileid,tileid_to_zxy
from pmtiles.writer import finalize_header
from pyproj import Transformer
import shapely
from shapely.geometry import box,shape,mapping,Point,LineString,MultiLineString,GeometryCollection
from shapely.ops import transform

from .admin_boundaries import read_source_level,bounded_coverage,encoded,immutable,require
from .core import ROOT,LOCAL,PUBLIC,digest
from .height_quality import evaluate_feature

VERSION='map-tiles-1.5'
DISPLAY_ID_HEX_LENGTH=16
HARD=1024*1024
LOW_TARGET=128*1024
DETAIL_TARGET=512*1024
EXTENT=8192
WORLD=40075016.68557849
HALF=WORLD/2
RESERVE=30*1024**3
SOURCE_CATALOG=LOCAL/'water-partition-20260920/18bc3c6798a76f0b3d72/catalog.json'
ADMIN_ARCHIVE=LOCAL/'admin-boundaries-20260920/raw/sgis-20250630.zip'
ADMIN_SHA='f1cf0f9de453ac7eaacb273f39cee52851183372b9ddfda428a967c3a670b2c6'
TOPICS={
    'admin-sido':(5,9),'admin-sigungu':(8,11),'admin-dong':(11,12),
    'land':(5,9),'water':(6,12),'roads':(6,13),'rail':(7,12),
    'facilities':(12,14),'buildings':(14,14),
}
PROJECT=Transformer.from_crs(4326,3857,always_xy=True)
NATIVE=Transformer.from_crs(5179,3857,always_xy=True)
TO_GEO=Transformer.from_crs(3857,4326,always_xy=True)


def sha(data):return hashlib.sha256(data).hexdigest()
def stable_id(source_id,identity):return sha((source_id+'\0'+str(identity)).encode())
def reserve(path,anticipated_bytes=64*1024**2):
    path=Path(path).resolve()
    while not path.exists():path=path.parent
    require(shutil.disk_usage(path).free>=RESERVE+anticipated_bytes,'30 GiB reserve would be violated')
def checkpoint(path,value):
    # New immutable progress records make interruptions inspectable.
    immutable(path,value)


def tile_bounds(z,x,y):
    width=WORLD/(2**z)
    return (-HALF+x*width,HALF-(y+1)*width,-HALF+(x+1)*width,HALF-y*width)


def bbox_tiles(bounds,z):
    a,b,c,d=bounds;n=2**z
    x0=max(0,min(n-1,math.floor((a+HALF)/WORLD*n)))
    x1=max(0,min(n-1,math.floor((c+HALF)/WORLD*n)))
    y0=max(0,min(n-1,math.floor((HALF-d)/WORLD*n)))
    y1=max(0,min(n-1,math.floor((HALF-b)/WORLD*n)))
    return [(x,y) for x in range(x0,x1+1) for y in range(y0,y1+1)]


def geometry_parts(geometry):
    if geometry.is_empty:return []
    if geometry.geom_type in ('Polygon','LineString','Point','MultiPolygon','MultiLineString','MultiPoint'):return [geometry]
    return [part for item in geometry.geoms for part in geometry_parts(item)]


def render_geometry(geometry,bounds,polygon=True):
    """Clip in Web Mercator and quantize once; never silently drop a source ID.

    If a subpixel ring cannot form a valid polygon on the MVT integer grid, keep
    its projected outline or a representative point. This is an explicit display
    representation; the original geometry hash and all properties are retained.
    """
    clipped=geometry.intersection(box(*bounds))
    if clipped.is_empty:return [],'outside'
    unit=(bounds[2]-bounds[0])/EXTENT
    # Validate the actual integer coordinates that MVT will receive. Validating
    # rounded metre floats first can hide a self-touch that appears on the second
    # integer rounding. This is the only quantization step.
    origin=np.asarray(bounds[:2])
    quantize=lambda g:shapely.transform(g,lambda coordinates:np.rint((coordinates-origin)/unit))
    snapped=quantize(clipped)
    notice='quantized'
    if polygon and snapped.geom_type in ('Polygon','MultiPolygon') and not snapped.is_valid:
        snapped=quantize(clipped.boundary);notice='quantized_outline'
    parts=[]
    for part in geometry_parts(snapped):
        if part.geom_type in ('Polygon','MultiPolygon') and part.area==0:continue
        if part.geom_type in ('LineString','MultiLineString') and part.length==0:continue
        parts.append(part)
    if not parts:
        parts=[quantize(clipped.representative_point())];notice='subpixel_anchor'
    return parts,notice


def encode_tile(topic,rows,z,x,y):
    bounds=tile_bounds(z,x,y);features=[];notices=Counter();source_ids=set()
    for stable,geometry,props in rows:
        parts,notice=render_geometry(geometry,bounds,not topic.startswith('admin-'))
        if not parts:continue
        source_ids.add(stable);notices[notice]+=1
        for part in parts:
            features.append({'geometry':part,'properties':{**props,'stable_id':stable[:DISPLAY_ID_HEX_LENGTH],'representation':notice}})
    if not features:return None,notices,source_ids
    payload=mapbox_vector_tile.encode({'name':topic,'features':features},default_options={
        'extents':EXTENT,'on_invalid_geometry':mapbox_vector_tile.encoder.on_invalid_geometry_raise})
    require(len(payload)<=HARD,f'Decoded single tile exceeds 1 MiB: {topic}/{z}/{x}/{y}, {len(payload)} bytes, {len(source_ids)} source records; split theme or raise its start zoom')
    # Decode once to prove no source identity disappeared during integer encoding.
    decoded=mapbox_vector_tile.decode(payload)[topic]['features']
    encoded_ids={feature['properties']['stable_id'] for feature in decoded}
    require(encoded_ids=={sid[:DISPLAY_ID_HEX_LENGTH] for sid in source_ids} and len(encoded_ids)==len(source_ids),'MVT encoder omitted a source feature or display identity collided')
    compressed=gzip.compress(payload,mtime=0)
    require(len(compressed)+512<=HARD,'Single tile cannot fit the 1 MiB archive limit')
    return compressed,notices,source_ids


def verify_display_ids(db,topic):
    previous=None;count=0
    for (full,) in db.execute('SELECT stable FROM records WHERE topic=? ORDER BY stable',(topic,)):
        prefix=full[:DISPLAY_ID_HEX_LENGTH]
        require(prefix!=previous,'Display identity prefix collision; publication refused')
        previous=prefix;count+=1
    return count


def compact_tile_ids(body):
    """Only rewrite stable_id value strings; original geometry commands stay exact."""
    message=vector_tile_pb2.tile();message.ParseFromString(gzip.decompress(body))
    for layer in message.layers:
        keys=list(layer.keys);key=keys.index('stable_id')
        indexes=set()
        other_indexes=set()
        for feature in layer.features:
            indexes.update(feature.tags[i+1] for i in range(0,len(feature.tags),2) if feature.tags[i]==key)
            other_indexes.update(feature.tags[i+1] for i in range(0,len(feature.tags),2) if feature.tags[i]!=key)
        require(not indexes.intersection(other_indexes),'Display ID value aliases an unrelated property')
        for index in indexes:
            value=layer.values[index]
            require(value.HasField('string_value') and len(value.string_value) in (16,64),'Unexpected existing display ID')
            value.string_value=value.string_value[:DISPLAY_ID_HEX_LENGTH]
    return gzip.compress(message.SerializeToString(deterministic=True),mtime=0)


def archive_bytes(tiles,topic,bounds):
    require(tiles,'Empty PMTiles archive')
    entries=[];bodies=[];offset=0;content={}
    for tileid,body in sorted(tiles):
        key=sha(body)
        if key in content:
            previous,data=content[key];require(data==body,'Tile hash collision');at=previous
        else:
            at=offset;content[key]=(at,body);bodies.append(body);offset+=len(body)
        entries.append(Entry(tileid,at,len(body),1))
    header={'tile_type':TileType.MVT,'tile_compression':Compression.GZIP,
        'min_lon_e7':round(bounds[0]*1e7),'min_lat_e7':round(bounds[1]*1e7),
        'max_lon_e7':round(bounds[2]*1e7),'max_lat_e7':round(bounds[3]*1e7),
        'center_zoom':tileid_to_zxy(tiles[0][0])[0],'center_lon_e7':round((bounds[0]+bounds[2])*5e6),'center_lat_e7':round((bounds[1]+bounds[3])*5e6)}
    parts=finalize_header(header,len(entries),entries,len(content),{'vector_layers':[{'id':topic,'fields':{'stable_id':'String','name':'String'}}]},True,offset)
    result=b''.join([*parts,*bodies]);require(len(result)<=HARD,'PMTiles archive exceeds 1 MiB')
    return result


def pack_archives(tiles,topic,bounds,folder,url_prefix):
    chunks=[];batch=[];size=0;last=-1
    def flush():
        nonlocal batch,size
        if not batch:return
        reserve(folder.parent if folder.parent.exists() else folder.parents[2])
        body=archive_bytes(batch,topic,bounds);name=f'{batch[0][0]}-{batch[-1][0]}-{sha(body)[:20]}.pmtiles'
        ref=immutable(folder/name,body);chunks.append({'url':url_prefix+'/'+name,'sha256':ref['sha256'],'byte_length':len(body),
            'first_tile_id':batch[0][0],'last_tile_id':batch[-1][0],'tile_count':len(batch)})
        batch=[];size=0
    for tileid,body in tiles:
        require(tileid>last,'Tiles must have unique sorted IDs');last=tileid
        target=LOW_TARGET if tileid_to_zxy(tileid)[0]<=9 else DETAIL_TARGET
        if batch and size+len(body)+16384>target:flush()
        batch.append((tileid,body));size+=len(body)
        if size+16384>=target:flush()
    flush();return chunks


def open_work(path,fingerprint):
    path.parent.mkdir(parents=True,exist_ok=True);db=sqlite3.connect(path,uri=True)
    db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA synchronous=NORMAL');db.execute('PRAGMA cache_size=-32768')
    db.executescript('''CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY,sha TEXT NOT NULL,count INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS records(n INTEGER PRIMARY KEY,topic TEXT NOT NULL,stable TEXT NOT NULL,geom BLOB NOT NULL,record BLOB NOT NULL,render TEXT NOT NULL,minzoom INTEGER NOT NULL,UNIQUE(topic,stable));
      CREATE VIRTUAL TABLE IF NOT EXISTS spatial USING rtree(n,minx,maxx,miny,maxy);
      CREATE INDEX IF NOT EXISTS topic_stable ON records(topic,stable);
      CREATE TABLE IF NOT EXISTS tiles(topic TEXT NOT NULL,tileid INTEGER NOT NULL,body BLOB NOT NULL,PRIMARY KEY(topic,tileid));
      CREATE TABLE IF NOT EXISTS stages(key TEXT PRIMARY KEY,value TEXT NOT NULL);''')
    old=db.execute('SELECT value FROM meta WHERE key=?',('fingerprint',)).fetchone()
    require(not old or old[0]==fingerprint,'Work checkpoint belongs to another input')
    db.execute('INSERT OR IGNORE INTO meta VALUES(?,?)',('fingerprint',fingerprint));db.commit();return db


def add_record(db,topic,identity,source,version,properties,geometry,source_asset,geometry_sha,minzoom=None,display_identity=None):
    sid=stable_id(source,identity if display_identity is None else display_identity)
    record={'stable_id':sid,'source_record_id':str(identity),'source_id':source,'version':version,
        'properties':properties,'geometry_sha256':geometry_sha,'source_asset_id':source_asset,
        'identity_scope':'source_record' if display_identity is None else 'source_record_and_geometry_fragment'}
    body=encoded(record);old=db.execute('SELECT record FROM records WHERE topic=? AND stable=?',(topic,sid)).fetchone()
    if old:
        previous=json.loads(zlib.decompress(old[0]));require(previous['geometry_sha256']==geometry_sha and previous['properties']==properties,'Conflicting source ID must not be merged');return False
    render={key:properties[key] for key in ('name','highway','class','kind','admin_level','admin_code','boundary_reference_date','code_namespace') if isinstance(properties.get(key),(str,int,float,bool))}
    render['source_id']=source
    cursor=db.execute('INSERT INTO records(topic,stable,geom,record,render,minzoom) VALUES(?,?,?,?,?,?)',
        (topic,sid,geometry.wkb,zlib.compress(body,1),json.dumps(render,ensure_ascii=False),minzoom if minzoom is not None else TOPICS[topic][0]))
    minx,miny,maxx,maxy=geometry.bounds;db.execute('INSERT INTO spatial VALUES(?,?,?,?,?)',(cursor.lastrowid,minx,maxx,miny,maxy));return True


def source_features(document):
    metadata=document.get('metadata')
    for feature in document.get('features',[]):
        raw=feature.get('properties') or {}
        if metadata and metadata.get('schema_version')==1 and 'rows' in metadata:
            index=raw.get('metadata_index');require(isinstance(index,int) and 0<=index<len(metadata['rows']),'Invalid compact properties')
            props={**metadata['shared'],**metadata['rows'][index],**{k:v for k,v in raw.items() if k!='metadata_index'}}
        else:props=raw
        yield feature,props


def classify(asset,props,geometry):
    if asset['layer']=='buildings':return 'buildings',TOPICS['buildings'][0]
    if asset['source_id']=='natural-earth' or asset['id']=='terrain-landmask-dokdo-osm':return 'land',5
    kind=props.get('kind');road=props.get('highway') or props.get('class')
    if kind in ('rail','railway','rail_line') or asset['layer']=='rail' and geometry.geom_type.endswith('LineString'):return 'rail',7
    if kind=='road' or props.get('highway') or asset['id'].startswith('road-'):
        rank=str(road).removesuffix('_link');return 'roads',{'motorway':6,'trunk':9,'primary':10,'secondary':11,'tertiary':12}.get(rank,12)
    if asset['layer']=='terrain' or kind in ('water','waterway','coastline'):return 'water',6
    return 'facilities',12


def source_inputs(catalog,proof=False,basemap=False):
    assets=[]
    for asset in catalog['assets']:
        if asset['format']!='geojson' or asset['layer'] not in ('terrain','infrastructure','rail'):continue
        if basemap:
            if asset['layer'] in ('infrastructure','rail') and asset.get('detail_level')!='overview':continue
            if asset['id'].startswith('road-'):continue
        elif asset.get('detail_level')=='overview':continue
        if asset['id'].startswith('osm-water-korea-part'):continue
        # Use the exact whole-water source once; the split representation is duplicate content.
        if '/water-partitions/' in asset['url']:continue
        path=(PUBLIC/asset['url'].removeprefix('/data/')).resolve()
        require(path.is_relative_to(PUBLIC.resolve()) and path.is_file(),'Source asset escaped or missing')
        assets.append({**asset,'path':str(path)})
    if not basemap:
        expected=json.loads((LOCAL/'retile/b908f45777c95a08/building-audit.json').read_bytes())['source_cells']
        connection=sqlite3.connect(f'file:{(LOCAL/"catalog.sqlite").as_posix()}?mode=ro',uri=True)
        registered={json.loads(row[0])['id']:json.loads(row[0]) for row in connection.execute("SELECT payload FROM assets WHERE id LIKE 'normalized-buildings-%'")};connection.close()
        for row in expected:
            asset=registered.get(row['id']);require(asset and asset['sha256']==row['source_sha256'] and asset['count']==row['source_count'],'Building source differs from audited national input')
            assets.append({**asset,'source_id':'overture','layer':'buildings','version':asset.get('version',asset.get('dataset_version','2026-08-19.0'))})
    if proof:
        seoul=(126.96,37.53,127.07,37.60)
        assets=[a for a in assets if a['bbox'][0]<=seoul[2] and a['bbox'][2]>=seoul[0] and a['bbox'][1]<=seoul[3] and a['bbox'][3]>=seoul[1]]
    return sorted(assets,key=lambda a:a['id'])


def ingest(db,assets,work,proof=False,basemap=False):
    clip=transform(PROJECT.transform,box(126.96,37.53,127.07,37.60)) if proof else None
    for i,asset in enumerate(assets):
        previous=db.execute('SELECT sha FROM sources WHERE id=?',(asset['id'],)).fetchone()
        if previous:require(previous[0]==asset['sha256'],'Resumed source hash differs');continue
        path=Path(asset['path']);require(path.is_file() and path.stat().st_size<256*1024**2,'Missing or over-budget source');reserve(work,max(64*1024**2,3*path.stat().st_size))
        raw=path.read_bytes();require(sha(raw)==asset['sha256'],'Source SHA mismatch')
        document=json.loads(raw);del raw;count=0
        require(len(document['features'])==asset['count'],'Source feature count mismatch')
        for ordinal,(feature,props) in enumerate(source_features(document)):
            require(feature.get('geometry'),'Source geometry missing; feature cannot be silently omitted')
            geom=shape(feature['geometry']);require(not geom.is_empty,'Empty source geometry')
            geometry=transform(PROJECT.transform,geom)
            if clip is not None and not geometry.intersects(clip):continue
            topic,minzoom=classify(asset,props,geometry)
            if basemap and topic not in ('land','water','roads','rail'):continue
            identity=feature.get('id') if feature.get('id') is not None else props.get('source_record_id')
            # Source collections without IDs (Natural Earth country outline)
            # receive an explicit file-hash/ordinal identity, not an invented ID.
            if identity is None:identity=f'file:{asset["sha256"]}:row:{ordinal}'
            if topic=='buildings':props={**props,**evaluate_feature(feature),'original_properties':props}
            geometry_sha=sha(encoded(feature['geometry']))
            # Existing road partitions can contain different fragments with the
            # same source ID. Keep both shapes; never overwrite one fragment.
            display_identity=None if topic=='buildings' else f'{identity}\0geometry:{geometry_sha}'
            count+=add_record(db,topic,identity,asset['source_id'],asset['version'],props,geometry,asset['id'],geometry_sha,minzoom,display_identity)
        db.execute('INSERT INTO sources VALUES(?,?,?)',(asset['id'],asset['sha256'],count));db.commit();del document
        if i%20==0:print(json.dumps({'stage':'source-index','done':i+1,'total':len(assets),'records':db.execute('SELECT COUNT(*) FROM records').fetchone()[0]}),flush=True)


def ingest_admin(db,level,work):
    topic='admin-'+('dong' if level=='eupmyeondong' else level)
    if db.execute('SELECT 1 FROM stages WHERE key=?',(topic+'-ingested',)).fetchone():return
    reserve(work);rows,geometries,source=read_source_level(ADMIN_ARCHIVE,level)
    fields={'sido':('SIDO_CD','SIDO_NM'),'sigungu':('SIGUNGU_CD','SIGUNGU_NM'),'eupmyeondong':('ADM_CD','ADM_NM')}
    code,name=fields[level]
    for row,geometry in zip(rows,geometries):
        require(row['BASE_DATE']=='20250630','Unexpected boundary date')
        props={**row,'name':row[name],'admin_code':row[code],'admin_level':level,'code_namespace':'sgis',
            'boundary_kind':'census_administrative','boundary_reference_date':'2025-06-30','legal_dong_code':None,'source_id':'sgis','evidence_type':'official_record'}
        add_record(db,topic,f'sgis:20250630:{level}:{row[code]}','sgis','2025-06-30',props,transform(NATIVE.transform,geometry),f'sgis-20250630-{level}',sha(geometry.wkb))
    db.execute('INSERT INTO stages VALUES(?,?)',(topic+'-ingested',json.dumps(source)));db.commit();del rows,geometries;gc.collect()


def reuse_admin(db,source_work,levels):
    """Reuse already certified original boundaries and tile bytes, not raw ZIPs."""
    source_work=Path(source_work).resolve();inputs=json.loads((source_work/'inputs.json').read_bytes())
    require(inputs['admin_sha256']==ADMIN_SHA,'Reused boundaries have another source release')
    source=sqlite3.connect(f'file:{(source_work/"index.sqlite").as_posix()}?mode=ro',uri=True)
    source.execute('BEGIN')
    require(source.execute("SELECT value FROM meta WHERE key='fingerprint'").fetchone()==(sha(encoded(inputs)),),'Reused work fingerprint differs')
    for level in levels:
        topic='admin-'+('dong' if level=='eupmyeondong' else level)
        if db.execute('SELECT 1 FROM stages WHERE key=?',(topic+'-ingested',)).fetchone():continue
        maximum=TOPICS[topic][1]
        require(source.execute('SELECT 1 FROM stages WHERE key=?',(f'{topic}-z{maximum}',)).fetchone(),'Boundary reuse is not fully certified yet')
        for geom,compressed in source.execute('SELECT geom,record FROM records WHERE topic=? ORDER BY stable',(topic,)):
            r=json.loads(zlib.decompress(compressed))
            add_record(db,topic,r['source_record_id'],r['source_id'],r['version'],r['properties'],shapely.from_wkb(geom),r['source_asset_id'],r['geometry_sha256'])
        verify_display_ids(db,topic)
        db.executemany('INSERT INTO tiles VALUES(?,?,?)',((t,tid,compact_tile_ids(body)) for t,tid,body in source.execute('SELECT topic,tileid,body FROM tiles WHERE topic=?',(topic,))))
        for key,value in source.execute('SELECT key,value FROM stages WHERE key=? OR key GLOB ?',(topic+'-ingested',topic+'-z[0-9]*')):
            audit=json.loads(value)
            if '-z' in key:
                z=int(key.rsplit('-z',1)[1]);first=(4**z-1)//3;last=(4**(z+1)-1)//3-1
                audit['maximum_compressed_tile_bytes']=db.execute('SELECT MAX(length(body)) FROM tiles WHERE topic=? AND tileid BETWEEN ? AND ?',(topic,first,last)).fetchone()[0]
                audit.update({'display_id_hex_length':DISPLAY_ID_HEX_LENGTH,'display_id_unique_verified':True,'geometry_commands_reused_without_change':True})
            db.execute('INSERT INTO stages VALUES(?,?)',(key,json.dumps(audit)))
        db.commit()
    source.close()


def fork_index(db,source_work,current):
    """Reuse captured geometry/properties after a display-only zoom policy change.

    The donor is attached read-only. Immutable inputs must match exactly. Tile
    checkpoints for the changed roads policy are deliberately not copied.
    """
    source_work=Path(source_work).resolve();old=json.loads((source_work/'inputs.json').read_bytes())
    require(old.get('version') in ('map-tiles-1.3','map-tiles-1.4') and old.get('display_id_hex_length')==DISPLAY_ID_HEX_LENGTH,'Unsupported checkpoint migration')
    changed=('roads','water') if old['version']=='map-tiles-1.3' else ('water',)
    for key in ('source_catalog_sha256','admin_sha256','levels','assets','proof_bbox','source_profile'):
        require(encoded(old.get(key))==encoded(current.get(key)),f'Checkpoint source contract differs: {key}')
    require(db.execute('SELECT COUNT(*) FROM records').fetchone()[0]==0,'Index fork requires empty destination')
    donor=f'file:{(source_work/"index.sqlite").as_posix()}?mode=ro'
    db.execute('ATTACH DATABASE ? AS donor',(donor,))
    try:
        require(db.execute("SELECT value FROM donor.meta WHERE key='fingerprint'").fetchone()==(sha(encoded(old)),),'Donor input identity changed')
        require(db.execute('SELECT COUNT(*) FROM donor.sources').fetchone()[0]==len(current['assets']),'Donor source ingestion is incomplete')
        db.execute('INSERT INTO records SELECT * FROM donor.records')
        db.execute('INSERT INTO spatial SELECT * FROM donor.spatial')
        db.execute('INSERT INTO sources SELECT * FROM donor.sources')
        if 'roads' in changed:db.execute("UPDATE records SET minzoom=CASE minzoom WHEN 7 THEN 9 WHEN 8 THEN 10 WHEN 9 THEN 11 WHEN 10 THEN 12 ELSE minzoom END WHERE topic='roads'")
        for topic, in db.execute('SELECT DISTINCT topic FROM records'):
            if topic not in changed:require(old['topics'][topic]==list(TOPICS[topic]),'Unreviewed theme zoom change')
        marks=','.join('?' for _ in changed)
        db.execute(f'INSERT INTO tiles SELECT * FROM donor.tiles WHERE topic NOT IN ({marks})',changed)
        predicates=' AND '.join('key NOT LIKE ?' for _ in changed)
        db.execute(f"INSERT INTO stages SELECT * FROM donor.stages WHERE key!='index-fork-proof' AND {predicates}",tuple(t+'-%' for t in changed))
        db.execute('INSERT INTO stages VALUES(?,?)',('index-fork-proof',json.dumps({'donor_inputs_sha256':digest(source_work/'inputs.json'),'geometry_and_selection_records_copied_exactly':True,'only_record_mutation':'roads.minzoom' if 'roads' in changed else None,'rebuilt_tile_topics':changed})))
        db.commit()
    except BaseException:
        db.rollback();raise
    finally:db.execute('DETACH DATABASE donor')


def tile_rows(db,topic,z,bounds):
    a,b,c,d=bounds
    # CROSS JOIN forces the RTree spatial search before record PK lookups.
    # Letting SQLite choose the topic index scans millions of records per tile.
    query='''SELECT r.n,r.stable,r.geom,r.render FROM spatial s CROSS JOIN records r ON r.n=s.n
      WHERE r.topic=? AND r.minzoom<=? AND s.minx<=? AND s.maxx>=? AND s.miny<=? AND s.maxy>=? ORDER BY r.stable'''
    return [(n,sid,shapely.from_wkb(geom),json.loads(render)) for n,sid,geom,render in db.execute(query,(topic,z,c,a,d,b))]


def build_topic_tiles(db,topic,work,zoom_range=None):
    minzoom,maxzoom=TOPICS[topic] if zoom_range is None else zoom_range;audit={};is_admin=topic.startswith('admin-')
    require(0<=minzoom<=maxzoom<=16,'Invalid topic zoom range')
    verify_display_ids(db,topic)
    originals=[];identities=[];properties=[];record_ids=[]
    largest_record=db.execute('SELECT COALESCE(MAX(n),0) FROM records').fetchone()[0]
    if is_admin:
        for n,sid,geom,render in db.execute('SELECT n,stable,geom,render FROM records WHERE topic=? ORDER BY stable',(topic,)):
            record_ids.append(n);originals.append(shapely.from_wkb(geom));identities.append(sid);properties.append(json.loads(render))
    for z in range(minzoom,maxzoom+1):
        stage=f'{topic}-z{z}'
        previous=db.execute('SELECT value FROM stages WHERE key=?',(stage,)).fetchone()
        if previous:audit[str(z)]=json.loads(previous[0]);continue
        # One byte per source record, not millions of 64-byte hash strings in
        # Python sets. This proof uses <7 MiB for the current national input.
        reserve(work);visited=set();notices=Counter();present=bytearray(largest_record+1);zoom_audit={'tile_count':0,'maximum_compressed_tile_bytes':0}
        if is_admin:
            allowed=WORLD/(2**z)/512*.4
            simplified,boundary_audit=bounded_coverage(originals,allowed)
            # One interior label anchor per source administrative feature. It is
            # a computed cartographic placement, never an official address point.
            geoms=[GeometryCollection([g.boundary,original.representative_point()]) for g,original in zip(simplified,originals)];index=shapely.STRtree(geoms)
            for g in geoms:visited.update(bbox_tiles(g.bounds,z))
            zoom_audit['boundary_error']=boundary_audit
            zoom_audit['quantization_max_px']=math.sqrt(2)/2*512/EXTENT
        else:
            for a,b,c,d in db.execute('SELECT s.minx,s.miny,s.maxx,s.maxy FROM spatial s JOIN records r ON r.n=s.n WHERE r.topic=? AND r.minzoom<=?',(topic,z)):
                visited.update(bbox_tiles((a,b,c,d),z))
        for ordinal,(x,y) in enumerate(sorted(visited,key=lambda xy:zxy_to_tileid(z,*xy))):
            if ordinal%128==0:reserve(work,256*1024**2)
            bounds=tile_bounds(z,x,y)
            if is_admin:rows=[(record_ids[i],identities[i],geoms[i],properties[i]) for i in index.query(box(*bounds),predicate='intersects')]
            else:
                rows=tile_rows(db,topic,z,bounds)
                if topic in ('roads','rail','detail-roads'):
                    tolerance=WORLD/(2**z)/512*.35
                    rows=[(n,sid,g.simplify(tolerance,preserve_topology=True),p) for n,sid,g,p in rows]
            body,tile_notices,ids=encode_tile(topic,[(sid,g,p) for n,sid,g,p in rows],z,x,y)
            if body is None:continue
            db.execute('INSERT OR REPLACE INTO tiles VALUES(?,?,?)',(topic,zxy_to_tileid(z,x,y),body))
            zoom_audit['tile_count']+=1;zoom_audit['maximum_compressed_tile_bytes']=max(zoom_audit['maximum_compressed_tile_bytes'],len(body));notices.update(tile_notices)
            for n,sid,_,_ in rows:
                if sid in ids:present[n]=1
        zoom_audit['represented_feature_count']=sum(present);zoom_audit['coverage_bitmap_bytes']=len(present);zoom_audit['representations']=dict(notices)
        zoom_audit.update({'display_id_hex_length':DISPLAY_ID_HEX_LENGTH,'display_id_unique_verified':True})
        if z==maxzoom:
            missing=sum(not present[n] for (n,) in db.execute('SELECT n FROM records WHERE topic=?',(topic,)))
            require(missing==0,f'{topic} maxzoom omitted {missing} source identities')
            zoom_audit['every_source_id_represented']=True
        db.execute('INSERT INTO stages VALUES(?,?)',(stage,json.dumps(zoom_audit)));db.commit();audit[str(z)]=zoom_audit
        print(json.dumps({'stage':'tiles','topic':topic,'zoom':z,**{k:v for k,v in zoom_audit.items() if k not in ('boundary_error','representations')}}),flush=True)
    return audit


def pack_details(db,topic,folder,url_prefix):
    refs=[];records=[];size=0
    def flush():
        nonlocal records,size
        if not records:return
        def write(batch):
            reserve(folder.parent if folder.parent.exists() else folder.parents[2])
            raw=encoded({'schema_version':1,'records':batch});data=gzip.compress(raw,mtime=0)
            if len(data)>DETAIL_TARGET and len(batch)>1:
                middle=len(batch)//2;write(batch[:middle]);write(batch[middle:]);return
            require(len(data)<=HARD and len(raw)<=8*1024**2,'Detail shard exceeds client decode budget')
            name=f'{batch[0]["stable_id"][:16]}-{sha(data)[:20]}.json.gz';ref=immutable(folder/name,data)
            refs.append({'url':url_prefix+'/'+name,'sha256':ref['sha256'],'byte_length':len(data),
                'first_id':batch[0]['stable_id'],'last_id':batch[-1]['stable_id'],'record_count':len(batch)})
        write(records)
        records=[];size=0
    for (compressed,) in db.execute('SELECT record FROM records WHERE topic=? ORDER BY stable',(topic,)):
        body=zlib.decompress(compressed)
        require(len(body)<HARD,'Single property record exceeds budget')
        # JSON decodes only for a selected feature; compact highly repetitive
        # properties by compressed-byte budget, with the client's 8 MiB cap.
        if records and size+len(body)+len(records)+64>7*1024*1024:flush()
        records.append(json.loads(body));size+=len(body)
    flush();return refs


def build(output,proof=False,levels=('sido','sigungu','eupmyeondong'),basemap=False,reuse_admin_work=None,reuse_index_work=None):
    require(not(proof and basemap),'Choose one source profile')
    catalog=json.loads(SOURCE_CATALOG.read_bytes());assets=source_inputs(catalog,proof,basemap)
    inputs={'version':VERSION,'source_catalog_sha256':digest(SOURCE_CATALOG),'admin_sha256':ADMIN_SHA,'levels':list(levels),
        'transform_sha256':digest(Path(__file__)),'admin_transform_sha256':digest(Path(__file__).with_name('admin_boundaries.py')),
        'assets':[(a['id'],a['sha256']) for a in assets],'topics':TOPICS,'extent':EXTENT,'display_id_hex_length':DISPLAY_ID_HEX_LENGTH,'proof_bbox':[126.96,37.53,127.07,37.60] if proof else None,
        'source_profile':'national-existing-overview-basemap' if basemap else 'seoul-proof' if proof else 'national-full',
        'reused_admin_inputs_sha256':digest(Path(reuse_admin_work)/'inputs.json') if reuse_admin_work else None,
        'reused_index_inputs_sha256':digest(Path(reuse_index_work)/'inputs.json') if reuse_index_work else None}
    fingerprint=sha(encoded(inputs));release='map2d-'+fingerprint[:20]
    output=Path(output);output.mkdir(parents=True,exist_ok=True);reserve(output)
    work=output.parent/'work'/fingerprint[:20];work.mkdir(parents=True,exist_ok=True)
    immutable(work/'inputs.json',inputs)
    immutable(work/'transform-snapshots/map_tiles.py',Path(__file__).read_bytes())
    immutable(work/'transform-snapshots/admin_boundaries.py',Path(__file__).with_name('admin_boundaries.py').read_bytes())
    require(digest(ADMIN_ARCHIVE)==ADMIN_SHA,'Original SGIS archive changed')
    db=open_work(work/'index.sqlite',fingerprint)
    if reuse_index_work and not db.execute('SELECT 1 FROM records LIMIT 1').fetchone():fork_index(db,reuse_index_work,inputs)
    if not reuse_admin_work:
        for level in levels:ingest_admin(db,level,work)
    ingest(db,assets,work,proof,basemap)
    if reuse_admin_work:reuse_admin(db,reuse_admin_work,levels)
    topics=[];audits={};base=output/'data/map-tiles'/release;url_prefix=f'/data/map-tiles/{release}'
    for (topic,) in db.execute('SELECT DISTINCT topic FROM records ORDER BY topic').fetchall():
        audits[topic]=build_topic_tiles(db,topic,work)
        count,a,b,c,d=db.execute('SELECT COUNT(*),MIN(s.minx),MIN(s.miny),MAX(s.maxx),MAX(s.maxy) FROM records r JOIN spatial s ON s.n=r.n WHERE r.topic=?',(topic,)).fetchone()
        lo,la=TO_GEO.transform(a,b);hi,ha=TO_GEO.transform(c,d);bounds=[lo,la,hi,ha]
        chunks=pack_archives(db.execute('SELECT tileid,body FROM tiles WHERE topic=? ORDER BY tileid',(topic,)),topic,bounds,base/topic/'tiles',url_prefix+'/'+topic+'/tiles')
        details=pack_details(db,topic,base/topic/'details',url_prefix+'/'+topic+'/details')
        minzoom,maxzoom=TOPICS[topic]
        topics.append({'id':topic,'source_layer':topic,'minzoom':minzoom,'maxzoom':maxzoom,'feature_count':count,'display_id_hex_length':DISPLAY_ID_HEX_LENGTH,
            'bounds':bounds,'chunks':chunks,'details':details,'description':'SGIS administrative boundaries; not legal-dong' if topic.startswith('admin-') else 'Source features with immutable selection properties',
            'geometry_precision':'Display MVT grid 8192; source geometry preserved separately; overzoom magnifies screen error'})
        print(json.dumps({'stage':'packed','topic':topic,'features':count,'archives':len(chunks),'details':len(details)}),flush=True)
    result={'schema_version':1,'release_id':release,'bounds':[124.5,33,132,38.7],'topics':topics,'sources':[
        *catalog['sources'],{'id':'sgis','title':'SGIS census administrative boundaries','url':'https://www.data.go.kr/data/15129688/fileData.do','license':'이용허락범위 제한 없음','description':'2025-06-30 administrative, not legal-dong boundaries'}],
        'source_release_id':catalog['release_id'],'reference_dates':{'sgis':'2025-06-30'},
        'attribution':'© OpenStreetMap contributors · Overture Maps · Natural Earth · SGIS 국가데이터처',
        'coverage':{'profile':inputs['source_profile'],'admin_scope':'nationwide','basemap_proof_bbox':inputs['proof_bbox'],
            'road_rail_geometry':'Previously published generalized overview source' if basemap else 'Original detail source',
            'buildings':'Not included in this early basemap' if basemap else 'All indexed source footprints'}}
    catalog_ref=immutable(base/'catalog.json',result);all_files=[]
    for path in sorted(base.rglob('*')):
        if path.is_file():all_files.append({'path':path.relative_to(output).as_posix(),'sha256':digest(path),'byte_length':path.stat().st_size})
    require(len(all_files)<3870,'Map file budget leaves no property-service headroom')
    require(all(f['byte_length']<24*1024**2 for f in all_files),'Static file exceeds 24 MiB safety limit')
    entry={'path':(base/'catalog.json').relative_to(output).as_posix(),'sha256':catalog_ref['sha256'],'release_id':release}
    report={'schema_version':1,'version':VERSION,'profile':inputs['source_profile'],'status':'validated','map_catalog':entry,'files':all_files,
        'file_count':len(all_files),'bytes':sum(f['byte_length'] for f in all_files),'sources':inputs,'topics':audits,
        'limits':{'archive_hard_bytes':HARD,'low_zoom_target_bytes':LOW_TARGET,'detail_target_bytes':DETAIL_TARGET},
        'limitations':['MVT coordinates are display geometry, not survey/address geometry.','Boundary levels use SGIS administrative codes, not legal-dong codes.','Source geometry is preserved in its original files, not copied into this data site.','Overzoom enlarges quantization and simplification in screen pixels.','No HTTP/GPU/publication verification performed by this offline build.']}
    immutable(output/'publication.json',report);db.close();return report


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,default=LOCAL/'map-tiles-20260920/candidate')
    parser.add_argument('--proof',action='store_true');parser.add_argument('--levels',default='sido,sigungu,eupmyeondong')
    parser.add_argument('--basemap',action='store_true');parser.add_argument('--reuse-admin-work',type=Path);parser.add_argument('--reuse-index-work',type=Path)
    args=parser.parse_args();report=build(args.output,args.proof,tuple(filter(None,args.levels.split(','))),args.basemap,args.reuse_admin_work,args.reuse_index_work)
    print(json.dumps({key:report[key] for key in ('status','profile','file_count','bytes','map_catalog')}),flush=True)


if __name__=='__main__':main()
