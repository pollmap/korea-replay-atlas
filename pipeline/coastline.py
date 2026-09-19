"""Stage Dokdo's source coastlines as terrain-draped land colour only.

No catalog, DEM, terrain height, original coastline or existing public file is
rewritten. Closed OSM ways retain every source node and remain a separate layer.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
from pathlib import Path
import osmium
from pyproj import Geod
from shapely.geometry import Polygon, box
from .core import LOCAL, PUBLIC, atomic_json, digest, now

VERSION = 'dokdo-landmask-1'
BOUNDS = (131.85, 37.23, 131.89, 37.26)
REQUIRED_WAYS = {456788679, 456788681}  # Seodo and Dongdo, verified OSM identities.


def read_coastline_snapshot(source: Path, bounds=BOUNDS):
    """Use sparse node reads instead of a nationwide node-location cache.

    All coastline candidates are examined; discovery is spatial and does not
    depend on a hardcoded list of small offshore rocks. A way crossing the
    requested boundary is rejected later rather than clipped or closed for it.
    """
    ways=[]
    for way in osmium.FileProcessor(source, entities=osmium.osm.WAY).with_filter(osmium.filter.TagFilter(('natural', 'coastline'))):
        if not way.nodes:
            continue
        ways.append({'id':way.id, 'version':way.version, 'timestamp':way.timestamp.isoformat(),
                     'refs':[node.ref for node in way.nodes], 'tags':dict(way.tags)})
    markers={way['refs'][0] for way in ways}
    first={node.id:(node.lon, node.lat) for node in osmium.FileProcessor(source, entities=osmium.osm.NODE).with_filter(osmium.filter.IdFilter(markers))}
    west,south,east,north=bounds
    selected=[way for way in ways if way['refs'][0] in first and
              west <= first[way['refs'][0]][0] <= east and south <= first[way['refs'][0]][1] <= north]
    refs={ref for way in selected for ref in way['refs']}
    points={node.id:(node.lon, node.lat) for node in osmium.FileProcessor(source, entities=osmium.osm.NODE).with_filter(osmium.filter.IdFilter(refs))}
    return selected, points, {'coastline_ways_scanned':len(ways), 'candidate_ways':len(selected), 'requested_nodes':len(refs), 'resolved_nodes':len(points)}


def build_landmask(ways, points, metadata, source_hash, *, bounds=BOUNDS, required_ids=REQUIRED_WAYS):
    ids=[way['id'] for way in ways]
    if len(ids)!=len(set(ids)):
        raise ValueError('Duplicate coastline source identity')
    if not required_ids.issubset(ids):
        raise ValueError('Required island coastlines are missing')
    if not 1 <= len(ways) <= 512 or sum(len(way['refs']) for way in ways)>50000:
        raise ValueError('Unexpected coastline workload')
    extent=box(*bounds);features=[];polygons=[];geod=Geod(ellps='WGS84');checks=[]
    for way in sorted(ways, key=lambda item:item['id']):
        refs=way['refs'];tags=way['tags']
        if tags.get('natural')!='coastline' or len(refs)<4 or refs[0]!=refs[-1]:
            raise ValueError(f"Open or non-coastline way: {way['id']}")
        if any(ref not in points for ref in refs):
            raise ValueError(f"Missing coastline node: {way['id']}")
        coords=[list(points[ref]) for ref in refs]
        if any(len(point)!=2 or not all(math.isfinite(v) for v in point) for point in coords):
            raise ValueError('Coastline must contain finite 2D source coordinates')
        polygon=Polygon(coords)
        if not polygon.is_valid or polygon.area<=0 or not polygon.exterior.is_ccw:
            raise ValueError(f"Invalid coastline polygon or land-side orientation: {way['id']}")
        if not extent.covers(polygon):
            raise ValueError(f"Coastline crosses the requested region: {way['id']}")
        if any(polygon.intersection(previous).area>1e-16 for previous in polygons):
            raise ValueError('Overlapping island coastlines')
        polygons.append(polygon)
        record=f"way/{way['id']}"
        properties={
            'name':tags.get('name:ko') or tags.get('name') or '독도 주변 바위',
            'kind':'land', 'kind_label':'해안선 육지 표시', 'natural':'coastline',
            'source_id':'osm', 'source_record_id':record, 'source_version':way['version'],
            'source_timestamp':way['timestamp'], 'source_url':'https://www.openstreetmap.org/'+record,
            'source_tags':dict(tags), 'source_node_ids':list(refs),
            'dataset_version':metadata['retrieved_at'][:10], 'retrieved_at':metadata['retrieved_at'],
            'input_hash':source_hash, 'transform_version':VERSION, 'evidence_type':'source_attribute',
            'observed_at':None, 'height_m':None, 'depth_m':None,
            'horizontal_geometry':'source_vertices_unchanged', 'vertical_geometry':'terrain_draped_display_only',
            'description':'OSM의 닫힌 해안선입니다. 기존 지형에 육지 색을 입히며 표고는 변경하지 않습니다.',
        }
        # Construct from the original coordinate sequence, with no simplification,
        # snapping, clipping, synthetic heights or topology repair.
        features.append({'type':'Feature', 'id':record, 'properties':properties,
                         'geometry':{'type':'Polygon', 'coordinates':[coords]}})
        checks.append({'source_record_id':record, 'source_version':way['version'], 'vertex_count':len(coords),
                       'valid':True, 'closed':True, 'counterclockwise':True,
                       'area_m2':abs(geod.geometry_area_perimeter(polygon)[0]), 'bbox':list(polygon.bounds)})
    collection={'type':'FeatureCollection', 'features':features, 'license':'ODbL-1.0',
                'source_url':metadata['url'], 'input_hash':source_hash, 'transform_version':VERSION,
                'description':'독도 해안선의 표시용 육지 마스크. DEM·표고·수직 기준은 변경하지 않습니다.'}
    result={'feature_count':len(features), 'vertex_count':sum(c['vertex_count'] for c in checks),
            'bbox':[min(p.bounds[0] for p in polygons), min(p.bounds[1] for p in polygons),
                    max(p.bounds[2] for p in polygons), max(p.bounds[3] for p in polygons)],
            'area_m2':sum(c['area_m2'] for c in checks), 'checks':checks,
            'source_vertices_changed':0, 'missing_nodes':0, 'height_values_created':0}
    return collection,result


def stage_dokdo(source: Path, *, public=PUBLIC, audit=LOCAL/'audit'):
    metadata=json.loads(source.with_suffix(source.suffix+'.meta.json').read_text(encoding='utf-8'))
    source_hash=digest(source)
    if source_hash!=metadata.get('sha256') or source.stat().st_size!=metadata.get('bytes'):
        raise ValueError('Coastline source checksum/size mismatch')
    ways,points,scan=read_coastline_snapshot(source)
    collection,quality=build_landmask(ways,points,metadata,source_hash)
    payload=json.dumps(collection,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
    sha=hashlib.sha256(payload).hexdigest();relative=Path('geography')/f'dokdo-landmask-{sha[:20]}.geojson';target=public/relative
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()!=payload:
            raise ValueError('Immutable coastline collision')
    else:
        with target.open('xb') as stream:
            stream.write(payload)
    descriptor={'id':'terrain-landmask-dokdo-osm','layer':'terrain','format':'geojson',
                'url':'/data/'+relative.as_posix(),'bbox':quality['bbox'],'source_id':'osm',
                'version':metadata['retrieved_at'][:10]+' / '+VERSION,'count':quality['feature_count'],
                'sha256':sha,'label':'독도 · OSM 해안선 육지 표시','detail_level':'detail',
                'min_camera_height':0,'max_camera_height':60000,'byte_length':len(payload),
                'feature_count':quality['feature_count'],'vertex_count':quality['vertex_count']}
    report={'schema_version':1,'created_at':now(),'passed':True,'publication_state':'staged_not_registered',
            'source_path':str(source),'source_url':metadata['url'],'source_sha256':source_hash,'license':'ODbL-1.0',
            'retrieved_at':metadata['retrieved_at'],'source_bbox':list(BOUNDS),'asset':descriptor,**scan,**quality,
            'limitations':['OSM coastline source attributes, not a surveyed elevation or guaranteed shoreline accuracy.',
                           'Ground colour only; original DEM, terrain vertices and negative elevations are preserved.',
                           'Separate source polygons do not erase the pre-existing low-resolution Natural Earth colour elsewhere.']}
    atomic_json(audit/'dokdo-landmask.json',report);atomic_json(audit/'dokdo-landmask-asset.json',descriptor)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--source',type=Path,default=LOCAL/'raw/osm/south-korea-20260916.osm.pbf')
    print(json.dumps(stage_dokdo(parser.parse_args().source),ensure_ascii=False))
