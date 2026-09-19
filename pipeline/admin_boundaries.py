"""Private SGIS boundary conversion with original codes, dates and geometry audit.

The census administrative classification is not a legal-dong/address registry.
Only the explicitly acquired public archive is read; this module never publishes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import struct
import zipfile

import numpy as np
from pyproj import CRS, Transformer
import shapely
from shapely.geometry import Polygon, MultiPolygon, Point, box, mapping
from shapely.ops import transform

VERSION = 'sgis-administrative-boundaries-1'
REFERENCE_DATE = '2025-06-30'
SOURCE_URL = 'https://www.data.go.kr/data/15129688/fileData.do'
DOWNLOAD_LIMIT = 300_000_000  # Parent authorized this one 269,032,521-byte ZIP.
EXTRACT_LIMIT = 1_000_000_000
OUTPUT_LIMIT = 20_000_000
FILE_LIMIT = 24 * 1024**2
RESERVE = 30 * 1024**3


def require(ok, message):
    if not ok:
        raise ValueError(message)


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def immutable(path, value):
    data = value if isinstance(value, bytes) else encoded(value)
    path = Path(path)
    require(not path.is_symlink(), 'Symlink output refused')
    if path.exists():
        require(path.read_bytes() == data, 'Existing output differs')
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as handle:
            handle.write(data)
    return {'file':path.name, 'byte_length':len(data), 'sha256':hashlib.sha256(data).hexdigest()}


def validate_members(archive):
    members = archive.infolist()
    require(len(members) < 10000, 'Excessive archive entries')
    names = set()
    for member in members:
        p = PurePosixPath(member.filename)
        require(not p.is_absolute() and '..' not in p.parts and chr(92) not in member.orig_filename and ':' not in member.filename,
                'Archive path escape')
        require(member.filename not in names, 'Duplicate archive member')
        require((member.external_attr >> 16) & 0o170000 != 0o120000, 'Archive symlink refused')
        require(not member.flag_bits & 1, 'Encrypted archive refused')
        names.add(member.filename)
    return members


def read_dbf(data, encoding):
    require(len(data) >= 33, 'Truncated DBF')
    count, header_size, record_size = struct.unpack_from('<IHH', data, 4)
    require(count <= 10000 and header_size >= 33 and (header_size-33) % 32 == 0, 'Invalid DBF header')
    require(len(data) >= header_size + count*record_size, 'Truncated DBF records')
    fields = []; offset = 1
    for start in range(32, header_size-1, 32):
        field = data[start:start+32]
        name = field[:11].split(b'\0')[0].decode('ascii')
        width = field[16]
        require(name and width and name not in [f[0] for f in fields], 'Invalid DBF field')
        fields.append((name, offset, width)); offset += width
    require(offset == record_size, 'DBF field lengths differ')
    result = []
    for index in range(count):
        row = data[header_size+index*record_size:header_size+(index+1)*record_size]
        require(row[:1] == b' ', 'Deleted/invalid DBF record')
        # Codes stay strings, including leading zeros. Preserve every source field.
        result.append({name:row[start:start+width].decode(encoding, errors='strict').rstrip(' '+chr(0)) for name,start,width in fields})
    return result


def rings_to_geometry(rings):
    require(rings, 'No polygon rings')
    shells = []; holes = []
    for ring in rings:
        require(len(ring) >= 4 and np.isfinite(ring).all() and np.array_equal(ring[0],ring[-1]), 'Unclosed/invalid source ring')
        p = Polygon(ring)
        require(p.is_valid and p.area > 0, 'Invalid source ring; automatic repair is forbidden')
        # ESRI Polygon outer rings are clockwise, holes counter-clockwise.
        (holes if p.exterior.is_ccw else shells).append(p)
    require(shells, 'No clockwise SHP shell')
    assigned = [[] for _ in shells]
    for hole in holes:
        parents = [i for i,shell in enumerate(shells) if shell.covers(hole)]
        require(parents, 'Hole not contained in a source shell')
        parent = min(parents, key=lambda i:shells[i].area)
        assigned[parent].append(list(hole.exterior.coords))
    polygons = [Polygon(shell.exterior.coords, assigned[i]) for i,shell in enumerate(shells)]
    geometry = polygons[0] if len(polygons)==1 else MultiPolygon(polygons)
    require(geometry.is_valid and not geometry.is_empty, 'Invalid source feature; automatic repair is forbidden')
    return geometry


def read_shp(handle, max_records=10000):
    header = handle.read(100)
    require(len(header)==100 and struct.unpack_from('>I',header)[0]==9994, 'Invalid SHP header')
    require(struct.unpack_from('<II',header,28)==(1000,5), 'Only 2D Polygon SHP is supported')
    declared = struct.unpack_from('>I',header,24)[0]*2
    read_bytes = 100; geometries = []
    while read_bytes < declared:
        record_header = handle.read(8)
        require(len(record_header)==8, 'Truncated SHP record header')
        number, words = struct.unpack('>II',record_header)
        require(number==len(geometries)+1 and 44<=words*2<=256_000_000, 'Invalid SHP record')
        record = handle.read(words*2); read_bytes += 8+len(record)
        require(len(record)==words*2 and struct.unpack_from('<I',record)[0]==5, 'Invalid SHP polygon record')
        parts, points = struct.unpack_from('<II',record,36)
        require(1<=parts<=points//4 and points<=10_000_000, 'Invalid SHP counts')
        require(44+4*parts+16*points==len(record), 'SHP coordinate length differs')
        starts = np.frombuffer(record,dtype='<i4',count=parts,offset=44)
        require(starts[0]==0 and np.all(np.diff(starts)>0), 'Invalid SHP ring offsets')
        xy = np.frombuffer(record,dtype='<f8',count=points*2,offset=44+4*parts).reshape((-1,2))
        stops = [*map(int,starts[1:]),points]
        geometries.append(rings_to_geometry([xy[int(a):b] for a,b in zip(starts,stops)]))
        require(len(geometries)<=max_records, 'SHP record budget exceeded')
    require(read_bytes==declared and not handle.read(1), 'SHP file length differs')
    return geometries


def geometry_rings(geometry):
    polygons = [geometry] if geometry.geom_type=='Polygon' else list(geometry.geoms)
    return [np.asarray(ring.coords) for polygon in polygons for ring in [polygon.exterior,*polygon.interiors]]


def vertex_count(geometry):
    return sum(len(ring) for ring in geometry_rings(geometry))


def ring_displacement_bound(original, simplified):
    """Conservative bidirectional curve bound in source metres, not a sample.

    Coverage simplification removes vertices. For each retained chord, all source
    vertices on its matching cyclic arc are measured against that chord. Convexity
    bounds every original segment; continuous projection bounds the reverse chord.
    New vertices, omitted rings or ambiguous arc matching are refused.
    """
    source = geometry_rings(original); target = geometry_rings(simplified)
    require(len(source)==len(target), 'Simplification removed a ring')
    candidates = []; envelopes=[]
    retained={tuple(p) for ring in target for p in ring[:-1]}
    for ring in source:
        lookup={}
        # Only retained coordinates need a Python index; the full source remains
        # a compact NumPy array. Millions of tuple objects are unnecessary.
        for i,p in enumerate(ring[:-1]):
            key=tuple(p)
            if key in retained:
                require(key not in lookup,'Repeated retained vertices require exact fallback')
                lookup[key]=i
        candidates.append((ring[:-1],lookup))
        lo=ring.min(axis=0);hi=ring.max(axis=0)
        envelopes.append(box(lo[0],lo[1],hi[0],hi[1]))
    index=shapely.STRtree(envelopes)
    used = set(); maximum = 0.0
    for ring in target:
        pairs = [tuple(p) for p in ring[:-1]]
        matches = [int(i) for i in index.query(Point(pairs[0])) if int(i) not in used and all(p in candidates[int(i)][1] for p in pairs)]
        require(len(matches)==1, 'Simplified ring cannot be matched uniquely')
        i = matches[0]; used.add(i); points,lookup = candidates[i]; n = len(points)
        indexes = [lookup[p] for p in pairs]
        closed = indexes+[indexes[0]]
        forward = sum((b-a)%n for a,b in zip(closed,closed[1:]))==n
        reverse = sum((a-b)%n for a,b in zip(closed,closed[1:]))==n
        require(forward or reverse, 'Simplified arc order changed')
        step = 1 if forward else -1
        for a,b in zip(closed,closed[1:]):
            length = ((b-a)*step)%n
            # Adjacent retained vertices are the exact original segment. Avoid
            # allocating NumPy arrays for the many unchanged fine-zoom chords.
            if length==1:continue
            arc = points[(a+step*np.arange(length+1))%n]
            p,q = points[a],points[b]; delta=q-p; squared=float(delta@delta)
            require(squared>0, 'Collapsed simplified chord')
            t=np.clip(((arc-p)@delta)/squared,0,1)
            distance=np.linalg.norm(arc-(p+t[:,None]*delta),axis=1)
            maximum=max(maximum,float(distance.max()))
    return maximum


def simplify_coverage(geometries, tolerance, maximum_error):
    require(tolerance>=0 and maximum_error>=0, 'Invalid simplification budget')
    source_vertices=sum(map(vertex_count,geometries))
    valid_coverage=bool(shapely.coverage_is_valid(geometries))
    note=None; bounds=[]; result=geometries;attempted_bound=None
    if tolerance and valid_coverage:
        candidate=list(shapely.coverage_simplify(geometries,tolerance))
        try:
            require(bool(shapely.coverage_is_valid(candidate)) and all(g.is_valid and not g.is_empty for g in candidate), 'Simplified coverage invalid')
            bounds=[ring_displacement_bound(a,b) for a,b in zip(geometries,candidate)]
            attempted_bound=max(bounds,default=0)
            require(max(bounds,default=0)<=maximum_error, 'Simplification exceeded measured error budget')
            result=candidate
        except ValueError as error:
            note=str(error);bounds=[0.0]*len(geometries)
    else:
        note='exact_source_requested' if not tolerance else 'source_is_not_an_edge_matched_coverage'
        bounds=[0.0]*len(geometries)
    return result, {'algorithm':'GEOS coverage Visvalingam-Whyatt; all-or-exact fallback',
        'tolerance_parameter_m':tolerance,'maximum_allowed_curve_bound_m':maximum_error,
        'maximum_curve_displacement_bound_m':max(bounds,default=0),'per_feature_curve_bound_m':bounds,
        'attempted_curve_displacement_bound_m':attempted_bound,
        'source_coverage_valid':valid_coverage,'fallback_reason':note,'source_vertices':source_vertices,
        'output_vertices':sum(map(vertex_count,result))}


def convert_features(rows, geometries, crs, level, tolerance=25, maximum_error=150):
    require(len(rows)==len(geometries) and rows, 'Attribute/shape count differs')
    require(CRS.from_user_input(crs).to_epsg(min_confidence=70)==5179, 'Expected official EPSG:5179 source CRS')
    code_size={'sido':2,'sigungu':5,'eupmyeondong':8}[level]
    prefix={'sido':'SIDO','sigungu':'SIGUNGU','eupmyeondong':'ADM'}[level]
    code_key,name_key=f'{prefix}_CD',f'{prefix}_NM'
    codes=[r.get(code_key,'') for r in rows]
    require(len(set(codes))==len(rows) and all(c.isdigit() and len(c)==code_size for c in codes), 'Invalid/duplicate SGIS codes')
    require(all(r.get(name_key) and r.get('BASE_DATE')=='20250630' for r in rows), 'Missing source name or unexpected boundary date')
    if level=='sido':require(len(rows)==17,'Expected 17 source provinces')
    simplified,audit=simplify_coverage(geometries,tolerance,maximum_error)
    forward=Transformer.from_crs(5179,4326,always_xy=True)
    inverse=Transformer.from_crs(4326,5179,always_xy=True)
    features=[]; roundtrip=0.0; wgs_geometries=[]
    for row,native,geometry in zip(rows,geometries,simplified):
        coords=shapely.get_coordinates(native)
        sample=coords[::max(1,len(coords)//1000)]
        lon,lat=forward.transform(sample[:,0],sample[:,1]);x,y=inverse.transform(lon,lat)
        roundtrip=max(roundtrip,float(np.hypot(x-sample[:,0],y-sample[:,1]).max()))
        wgs=transform(forward.transform,geometry)
        wgs=shapely.orient_polygons(wgs,exterior_cw=False)
        wgs_geometries.append(wgs)
        require(wgs.is_valid and not wgs.is_empty,'WGS84 geometry invalid')
        lo,la,hi,ha=wgs.bounds
        require(123<lo<hi<133 and 32<la<ha<40,'Boundary outside expected Korea extent')
        properties={**row,'name':row[name_key],'admin_code':row[code_key],'admin_level':level,
                    'code_namespace':'sgis','boundary_kind':'census_administrative','boundary_reference_date':REFERENCE_DATE,
                    'source_id':'sgis-admin-20250630','legal_dong_code':None}
        features.append({'type':'Feature','id':f'sgis:20250630:{level}:{row[code_key]}',
                         'properties':properties,'bbox':list(wgs.bounds),'geometry':mapping(wgs)})
    features.sort(key=lambda f:f['properties']['admin_code'])
    require(bool(shapely.coverage_is_valid(wgs_geometries)) or not audit['source_coverage_valid'],'WGS84 transform broke shared boundary coverage')
    audit.update(feature_count=len(features),source_code_field=code_key,source_name_field=name_key,source_code_name_sha256=hashlib.sha256(encoded(sorted((r[code_key],r[name_key]) for r in rows))).hexdigest(),
                 source_crs='EPSG:5179',output_crs='OGC:CRS84 (WGS84 longitude,latitude)',
                 roundtrip_sample_max_m=roundtrip,source_fields_preserved=True,
                 wgs84_coordinate_quantization=False,
                 source_ring_count=sum(len(geometry_rings(g)) for g in geometries),
                 output_ring_count=sum(len(geometry_rings(g)) for g in simplified))
    # `metadata` is reserved by the map loader for its shared/rows dictionary.
    return {'type':'FeatureCollection','features':features,'source_metadata':{'schema_version':1,'source_url':SOURCE_URL,
        'reference_date':REFERENCE_DATE,'boundary_kind':'census_administrative','code_namespace':'sgis','source_crs':'EPSG:5179',
        'simplification_curve_bound_m':audit['maximum_curve_displacement_bound_m']}},audit


def bounded_coverage(geometries, maximum_error, attempts=12):
    """Calibrate shared-arc VW tolerance, never return an unbudgeted exact level.

    Installed GEOS/Shapely accepts one tolerance per coverage, not per feature.
    Retained arcs are measured against all their source vertices. A failed
    measurement reduces the shared tolerance and retries; exhausted attempts
    fail explicitly instead of silently emitting a huge original GeoJSON.
    The error is relative to the supplied projected source polylines, not survey
    accuracy. This function deliberately does not repair or drop source rings.
    """
    require(maximum_error>0,'Positive screen error budget required')
    require(bool(shapely.coverage_is_valid(geometries)),'Boundary coverage is not edge matched')
    tolerance=maximum_error/32
    notes={};bounds=[0.0]*len(geometries)
    for attempt in range(attempts+1):
        candidate=list(shapely.coverage_simplify(geometries,tolerance))
        require(bool(shapely.coverage_is_valid(candidate)),'Shared arc simplification broke coverage')
        failures=[]
        for i,(original,result) in enumerate(zip(geometries,candidate)):
            try:
                if original.equals_exact(result,0):bounds[i]=0
                else:bounds[i]=ring_displacement_bound(original,result)
                require(bounds[i]<=maximum_error,'Measured shared arc bound exceeded')
            except ValueError as error:
                failures.append(i);notes[str(i)]=str(error)
        if not failures:
            return candidate,{'algorithm':'GEOS shared arcs with measured tolerance calibration',
                'maximum_allowed_curve_bound_m':maximum_error,'maximum_curve_displacement_bound_m':max(bounds,default=0),
                'per_feature_curve_bound_m':bounds,'per_feature_tolerance_m':[tolerance]*len(geometries),
                'calibration_rounds':attempt+1,'exact_feature_count':sum(a.equals_exact(b,0) for a,b in zip(geometries,candidate)),
                'source_vertices':sum(map(vertex_count,geometries)),'output_vertices':sum(map(vertex_count,candidate)),
                'source_ring_count':sum(len(geometry_rings(g)) for g in geometries),
                'output_ring_count':sum(len(geometry_rings(g)) for g in candidate),'calibration_notes':notes}
        tolerance/=2
    raise ValueError('Could not certify the boundary screen error')


def read_source_level(archive_path, level):
    """Read one original level without extracting/copying the national archive."""
    token={'sido':'bnd_sido','sigungu':'bnd_sigungu','eupmyeondong':'bnd_dong'}[level]
    with zipfile.ZipFile(archive_path) as archive:
        members=validate_members(archive)
        matches=[m for m in members if token in PurePosixPath(m.filename).name.lower() and m.filename.endswith('.shp')]
        require(len(matches)==1,'Ambiguous SGIS source layer')
        member=matches[0];stem=member.filename[:-4]
        require(member.file_size<=EXTRACT_LIMIT,'SGIS layer exceeds extraction budget')
        crs=CRS.from_wkt(archive.read(stem+'.prj').decode('utf-8-sig'))
        require(crs.to_epsg(min_confidence=70)==5179,'Unexpected SGIS source CRS')
        encoding=archive.read(stem+'.cpg').decode().strip() if stem+'.cpg' in archive.namelist() else 'cp949'
        rows=read_dbf(archive.read(stem+'.dbf'),'cp949' if encoding=='949' else encoding)
        with archive.open(member) as handle:geometries=read_shp(handle)
        require(len(rows)==len(geometries),'SGIS attributes and geometry counts differ')
        return rows,geometries,{'member':member.filename,'bytes':member.file_size,'crs':'EPSG:5179','crc32':f'{member.CRC:08x}'}


def build(archive_path, output, tolerance=25, maximum_error=150):
    archive_path=Path(archive_path);output=Path(output)
    require(archive_path.is_file() and not archive_path.is_symlink() and archive_path.stat().st_size<=DOWNLOAD_LIMIT,'Invalid original archive')
    require(shutil.disk_usage(archive_path.parent).free>=RESERVE+OUTPUT_LIMIT,'30 GiB reserve would be violated')
    require(not output.exists(),'Candidate output must be new')
    with archive_path.open('rb') as f:original_sha=hashlib.file_digest(f,'sha256').hexdigest()
    report={'schema_version':1,'version':VERSION,'source_url':SOURCE_URL,'reference_date':REFERENCE_DATE,
            'raw_archive_sha256':original_sha,'raw_archive_bytes':archive_path.stat().st_size,'layers':{},'status':'preparing'}
    payloads={}
    with zipfile.ZipFile(archive_path) as archive:
        members=validate_members(archive)
        layers={}
        for level,token in [('sido','bnd_sido'),('sigungu','bnd_sigungu')]:
            found=[m for m in members if token in PurePosixPath(m.filename).name.lower() and m.filename.lower().endswith('.shp')]
            require(len(found)==1,f'Expected one {level} SHP, got {len(found)}')
            stem=found[0].filename[:-4]
            selected={ext:archive.getinfo(stem+ext) for ext in ('.shp','.dbf','.prj')}
            if stem+'.cpg' in archive.namelist():selected['.cpg']=archive.getinfo(stem+'.cpg')
            layers[level]=selected
        require(sum(m.file_size for selected in layers.values() for m in selected.values())<=EXTRACT_LIMIT,'Selected boundary extraction exceeds 1 GB')
        for level,selected in layers.items():
            crs=CRS.from_wkt(archive.read(selected['.prj']).decode('utf-8-sig'))
            encoding=archive.read(selected['.cpg']).decode().strip() if '.cpg' in selected else 'cp949'
            if encoding=='949':encoding='cp949'
            rows=read_dbf(archive.read(selected['.dbf']),encoding)
            with archive.open(selected['.shp']) as f:geometries=read_shp(f)
            data,audit=convert_features(rows,geometries,crs,level,tolerance,maximum_error)
            audit['source_members']={ext:{'name':m.filename,'bytes':m.file_size,'crc32':f'{m.CRC:08x}'} for ext,m in selected.items()}
            payload=encoded(data);require(len(payload)<FILE_LIMIT,'Individual boundary file exceeds 24 MiB')
            payloads[level]=payload;report['layers'][level]=audit
            print(json.dumps({'stage':level,'feature_count':len(rows),'bytes':len(payload),'curve_bound_m':audit['maximum_curve_displacement_bound_m'],
                              'vertices':audit['output_vertices'],'source_vertices':audit['source_vertices']},ensure_ascii=False),flush=True)
    require(sum(map(len,payloads.values()))+len(encoded(report))+10000<=OUTPUT_LIMIT,'Candidate exceeds 20 MB')
    output.mkdir(parents=True)
    for level,payload in payloads.items():report['layers'][level]['asset']=immutable(output/f'{level}.geojson',payload)
    report['status']='validated';report['candidate_bytes']=sum(map(len,payloads.values()))
    report['legal_dong_available']=False
    immutable(output/'audit.json',report)
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--tolerance',type=float,default=25);parser.add_argument('--maximum-error',type=float,default=150)
    args=parser.parse_args();report=build(args.archive,args.output,args.tolerance,args.maximum_error)
    print(json.dumps(report,ensure_ascii=False))


if __name__=='__main__':main()
