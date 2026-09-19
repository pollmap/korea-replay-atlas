"""Stage source-faithful, spatially partitioned OSM infrastructure for review.

No catalog mutation: infrastructure is a distinct layer, never terrain or live traffic.
"""
from __future__ import annotations
import argparse
from collections import Counter, OrderedDict
import json
import math
import re
from pathlib import Path
import uuid
import osmium
from shapely.geometry import Point, LineString, box, mapping, shape
from .core import LOCAL, PUBLIC, atomic_json, digest, now

VERSION = 'infrastructure-1'
MAJOR_ROADS = {'motorway', 'trunk', 'primary', 'secondary', 'motorway_link', 'trunk_link', 'primary_link', 'secondary_link'}
ROAD_TYPES = MAJOR_ROADS | {'tertiary', 'tertiary_link', 'unclassified', 'residential', 'living_street', 'service', 'pedestrian', 'track', 'footway', 'cycleway', 'path', 'steps', 'road', 'bridleway'}
AREA_TYPES = {'airport', 'airport_runway', 'airport_taxiway', 'airport_apron', 'airport_terminal', 'industrial_land', 'port', 'pier', 'ferry_terminal', 'station', 'bus_station', 'platform', 'bus_stop'}
LABELS = {'road':'도로', 'airport':'공항', 'airport_runway':'활주로', 'airport_taxiway':'유도로', 'airport_apron':'계류장', 'airport_terminal':'공항 터미널', 'industrial_land':'산업 용지 (OSM)', 'port':'항만', 'pier':'부두', 'ferry_terminal':'여객선 터미널', 'ferry_route':'여객선 항로', 'station':'철도역', 'bus_station':'버스 터미널', 'bus_stop':'버스 정류장', 'platform':'승강장', 'settlement':'도시·마을'}


def categories(tags: dict, geometry: str) -> list[str]:
    """Read explicit OSM tags; construction/disused infrastructure is not active."""
    if tags.get('disused') == 'yes' or tags.get('abandoned') == 'yes':
        return []
    result = []
    highway = tags.get('highway')
    if geometry == 'line' and highway in ROAD_TYPES:
        result.append('road')
    aeroway = tags.get('aeroway')
    airports = {'aerodrome':'airport', 'runway':'airport_runway', 'taxiway':'airport_taxiway', 'apron':'airport_apron', 'terminal':'airport_terminal'}
    if aeroway in airports:
        result.append(airports[aeroway])
    if tags.get('landuse') == 'industrial' and geometry == 'area':
        result.append('industrial_land')
    if tags.get('landuse') == 'port' or tags.get('industrial') == 'port' or tags.get('harbour') in ('yes', 'port'):
        result.append('port')
    if tags.get('man_made') == 'pier':
        result.append('pier')
    amenities = {'ferry_terminal':'ferry_terminal', 'bus_station':'bus_station'}
    if tags.get('amenity') in amenities:
        result.append(amenities[tags['amenity']])
    if tags.get('route') == 'ferry' and geometry == 'line':
        result.append('ferry_route')
    if tags.get('railway') in ('station', 'halt'):
        result.append('station')
    if highway == 'bus_stop' or (tags.get('public_transport') == 'stop_position' and tags.get('bus') == 'yes'):
        result.append('bus_stop')
    if (tags.get('railway') == 'platform' or tags.get('public_transport') == 'platform') and 'bus_stop' not in result:
        result.append('platform')
    if geometry == 'point' and tags.get('place') in ('city', 'town', 'village', 'suburb', 'quarter'):
        result.append('settlement')
    return list(dict.fromkeys(result))


def overview_visible(kind: str, tags: dict) -> bool:
    if kind == 'road':
        return tags.get('highway') in MAJOR_ROADS
    if kind == 'settlement':
        return tags.get('place') in ('city', 'town')
    return kind in {'airport', 'airport_runway', 'port', 'ferry_terminal', 'ferry_route', 'industrial_land', 'station', 'bus_station'}


def cell_parts(geometry, size: float):
    """Clip crossing features into every intersected cell, retaining source identity."""
    if isinstance(geometry, Point):
        x, y = math.floor(geometry.x/size), math.floor(geometry.y/size)
        yield x, y, geometry
        return
    west, south, east, north = geometry.bounds
    for x in range(math.floor(west/size), math.floor(east/size)+1):
        for y in range(math.floor(south/size), math.floor(north/size)+1):
            fragment = geometry.intersection(box(x*size, y*size, (x+1)*size, (y+1)*size))
            if fragment.is_empty:
                continue
            # A polygon touching an adjacent cell only along a boundary is not an area.
            if geometry.geom_type in ('Polygon', 'MultiPolygon') and fragment.area == 0:
                continue
            if geometry.geom_type in ('LineString', 'MultiLineString') and fragment.length == 0:
                continue
            yield x, y, fragment


class Spool:
    def __init__(self, directory: Path):
        self.directory = directory
        self.handles = OrderedDict()
        self.counts = Counter()

    def add(self, tier: str, x: int, y: int, feature: dict):
        key = f'{tier}-{x}-{y}'
        if key not in self.handles:
            if len(self.handles) >= 48:
                _, old = self.handles.popitem(last=False)
                old.close()
            self.handles[key] = (self.directory / f'{key}.ndjson').open('a', encoding='utf-8')
        self.handles.move_to_end(key)
        self.handles[key].write(json.dumps(feature, ensure_ascii=False, allow_nan=False, separators=(',', ':'))+'\n')
        self.counts[key] += 1

    def close(self):
        for handle in self.handles.values():
            handle.close()
        self.handles.clear()


def extract_infrastructure(path: Path, *, supplementary_airfields: bool = False) -> dict:
    metadata = json.loads(path.with_suffix(path.suffix+'.meta.json').read_text(encoding='utf-8'))
    actual_hash = digest(path)
    if actual_hash != metadata['sha256']:
        raise ValueError('PBF checksum differs from source metadata')
    fingerprint = actual_hash[:12] + ('-airfields' if supplementary_airfields else '')
    version = metadata['retrieved_at'][:10]
    run = LOCAL/'silver'/'infrastructure'/f'{fingerprint}-{VERSION}'/uuid.uuid4().hex[:10]
    run.mkdir(parents=True, exist_ok=False)
    spool = Spool(run)
    counts, errors, tag_counts = Counter(), Counter(), Counter()
    quarantined = []
    factory = osmium.geom.GeoJSONFactory()
    seen_areas = set()

    def select(tags, geometry):
        selected = categories(tags, geometry)
        if supplementary_airfields:
            return [kind for kind in selected if kind.startswith('airport')] if tags.get('military') is not None else []
        return selected

    def emit(identifier, tags, geom, kinds):
        if geom.is_empty or not geom.is_valid:
            errors['invalid_geometry'] += 1
            quarantined.append({'source_record_id':identifier, 'reason':'invalid_geometry'})
            return
        bounds = geom.bounds
        if not all(math.isfinite(v) for v in bounds) or bounds[0] < -180 or bounds[2] > 180 or bounds[1] < -90 or bounds[3] > 90:
            errors['invalid_coordinates'] += 1
            quarantined.append({'source_record_id':identifier, 'reason':'invalid_coordinates'})
            return
        for kind in kinds:
            counts[kind] += 1
            for attr in ('bridge', 'tunnel'):
                if tags.get(attr) not in (None, 'no'):
                    tag_counts[attr] += 1
            properties = {
                'name': tags.get('name', ''), 'kind': kind, 'kind_label': LABELS[kind],
                'source_id': 'osm', 'source_record_id': identifier,
                'source_url': 'https://www.openstreetmap.org/'+identifier,
                'dataset_version': version, 'retrieved_at': metadata['retrieved_at'],
                'input_hash': actual_hash, 'transform_version': VERSION,
                'evidence_type': 'source_attribute', 'observed_at': None,
                'height_m': None, 'depth_m': None,
                'vertical_geometry': 'unavailable',
                'description': 'OSM 등록 지표 형상입니다. 운행 위치·실측 교량 높이·터널 심도는 포함하지 않습니다.' if kind != 'industrial_land' else 'OSM landuse=industrial 용지입니다. 법정 산업단지 지정·경계 확인 자료가 아닙니다.',
            }
            for key in ('name:en', 'operator', 'ref', 'highway', 'railway', 'aeroway', 'bridge', 'tunnel', 'layer', 'access', 'surface', 'lanes', 'maxspeed', 'place', 'wikidata', 'iata', 'icao'):
                if key in tags:
                    properties[key] = tags[key]
            for tier, size in [('detail', .25), ('overview', 1.0)]:
                if tier == 'overview' and not overview_visible(kind, tags):
                    continue
                display = geom.simplify(.0002, preserve_topology=True) if tier == 'overview' else geom
                for x, y, fragment in cell_parts(display, size):
                    feature = {'type':'Feature', 'id':f'{identifier}:{kind}:{x}:{y}', 'geometry':mapping(fragment), 'properties':{**properties, 'detail_level':tier, 'horizontal_geometry':'simplified' if tier == 'overview' else 'source_vertices_clipped'}}
                    spool.add(tier, x, y, feature)

    class Handler(osmium.SimpleHandler):
        def node(self, node):
            if not node.tags:
                return
            tags = dict(node.tags)
            kinds = select(tags, 'point')
            if not kinds:
                return
            if not node.location.valid():
                errors['node_missing_location'] += 1
                quarantined.append({'source_record_id':f'node/{node.id}', 'reason':'node_missing_location'})
                return
            emit(f'node/{node.id}', tags, Point(node.location.lon, node.location.lat), kinds)

        def way(self, way):
            tags = dict(way.tags)
            kinds = select(tags, 'line')
            # Closed area-tagged ways are assembled in area() with their holes.
            if way.is_closed() and tags.get('area') != 'no':
                kinds = [kind for kind in kinds if kind not in AREA_TYPES and tags.get('area') != 'yes']
            if not kinds:
                return
            try:
                coords = [(node.lon, node.lat) for node in way.nodes]
                if len(coords) < 2:
                    errors['short_way'] += 1
                    quarantined.append({'source_record_id':f'way/{way.id}', 'reason':'fewer_than_two_way_nodes', 'node_refs':[node.ref for node in way.nodes]})
                    return
                emit(f'way/{way.id}', tags, LineString(coords), kinds)
            except osmium.InvalidLocationError:
                errors['way_missing_location'] += 1
                quarantined.append({'source_record_id':f'way/{way.id}', 'reason':'way_missing_location'})

        def area(self, area):
            tags = dict(area.tags)
            kinds = select(tags, 'area')
            if not kinds:
                return
            identifier = f'{"way" if area.from_way() else "relation"}/{area.orig_id()}'
            if identifier in seen_areas:
                errors['duplicate_area'] += 1
                return
            seen_areas.add(identifier)
            try:
                geom = shape(json.loads(factory.create_multipolygon(area)))
                emit(identifier, tags, geom, kinds)
            except (RuntimeError, ValueError):
                errors['area_assembly'] += 1
                quarantined.append({'source_record_id':identifier, 'reason':'area_assembly'})

    try:
        # libosmium retains every node for geometry assembly before these filters;
        # only potentially useful tagged entities cross the Python callback boundary.
        keys = ('aeroway',) if supplementary_airfields else ('highway', 'aeroway', 'landuse', 'industrial', 'harbour', 'man_made', 'amenity', 'route', 'railway', 'public_transport', 'place')
        key_filter = osmium.filter.KeyFilter(*keys)
        Handler().apply_file(str(path), locations=True, idx='flex_mem', filters=[key_filter])
    finally:
        spool.close()
    output_dir = PUBLIC/'infrastructure'/f'{fingerprint}-{VERSION}'
    output_dir.mkdir(parents=True, exist_ok=True)
    assets = []
    for key, count in sorted(spool.counts.items()):
        match = re.fullmatch(r'(detail|overview)-(-?\d+)-(-?\d+)', key)
        if match is None:
            raise ValueError('Invalid infrastructure cell key')
        tier, x_text, y_text = match.groups()
        size = .25 if tier == 'detail' else 1.0
        x, y = int(x_text), int(y_text)
        features = [json.loads(line) for line in (run/f'{key}.ndjson').read_text(encoding='utf-8').splitlines()]
        filename = output_dir/f'{key}.geojson'
        atomic_json(filename, {'type':'FeatureCollection', 'features':features})
        asset_prefix = 'infrastructure-airfields' if supplementary_airfields else 'infrastructure'
        assets.append({'id':f'{asset_prefix}-{key}', 'layer':'infrastructure', 'format':'geojson', 'url':'/data/'+filename.relative_to(PUBLIC).as_posix(), 'bbox':[x*size, y*size, (x+1)*size, (y+1)*size], 'source_id':'osm', 'version':version, 'count':count, 'sha256':digest(filename), 'label':f'공개 기반 시설 · {tier}', 'detail_level':tier, 'min_camera_height':60000 if tier == 'overview' else 0, 'max_camera_height':1500000 if tier == 'overview' else 60000, 'bytes':filename.stat().st_size})
    report = {'schema_version':1, 'created_at':now(), 'source_sha256':actual_hash, 'source_url':metadata['url'], 'license':'ODbL-1.0', 'transform_version':VERSION, 'source_feature_counts':dict(counts), 'vertical_tag_counts':dict(tag_counts), 'errors':dict(errors), 'asset_count':len(assets), 'fragment_count':sum(a['count'] for a in assets), 'bytes':sum(a['bytes'] for a in assets), 'assets':assets, 'publication_state':'staged_not_registered', 'limitations':['OSM coverage varies; absent features are not proven absent on the ground.', 'Static source geometry only; no live traffic, structure height or tunnel depth.', 'Industrial land polygons are not official designated industrial park boundaries.', 'Relation-based ferry route aggregation and building geometry are not included.']}
    manifest = LOCAL/'audit'/('infrastructure-airfields-korea.json' if supplementary_airfields else 'infrastructure-korea.json')
    report['quarantine'] = quarantined
    atomic_json(manifest, report)
    print(json.dumps({k:v for k,v in report.items() if k != 'assets'}, ensure_ascii=False))
    return report


def merge_staged_supplement(manifest: Path, supplement: Path) -> None:
    """Merge a bounded supplemental extraction before any catalog publication."""
    base = json.loads(manifest.read_text(encoding='utf-8'))
    extra = json.loads(supplement.read_text(encoding='utf-8'))
    if base['source_sha256'] != extra['source_sha256']:
        raise ValueError('Supplement must use the same PBF source')
    assets = {asset['id']:asset for asset in base['assets']}
    added = 0
    for asset in extra['assets']:
        if asset['id'] in assets:
            if assets[asset['id']]['sha256'] != asset['sha256']:
                raise ValueError('Conflicting supplemental asset')
        else:
            assets[asset['id']] = asset
            added += 1
    if added:
        base['assets'] = sorted(assets.values(), key=lambda asset:asset['id'])
        for key in ('source_feature_counts', 'vertical_tag_counts', 'errors'):
            base[key] = dict(Counter(base.get(key, {}))+Counter(extra.get(key, {})))
        base['asset_count'] = len(assets)
        base['fragment_count'] = sum(asset['count'] for asset in assets.values())
        base['bytes'] = sum(asset['bytes'] for asset in assets.values())
        base['supplemental_manifests'] = [str(supplement)]
        atomic_json(manifest, base)


def validate_staged(manifest: Path | None = None, source_path: Path | None = None) -> dict:
    """Validate staged geometry and coalesce dual line/area forms before promotion."""
    manifest = manifest or LOCAL/'audit'/'infrastructure-korea.json'
    report = json.loads(manifest.read_text(encoding='utf-8'))
    originals = {}
    removed = 0
    kinds = Counter()
    for asset in report['assets']:
        path = PUBLIC/asset['url'].removeprefix('/data/')
        if digest(path) != asset['sha256']:
            raise ValueError('Staged infrastructure checksum mismatch')
        collection = json.loads(path.read_text(encoding='utf-8'))
        unique = {}
        bounds = box(*asset['bbox']).buffer(1e-10)
        for feature in collection['features']:
            geom = shape(feature['geometry'])
            if not geom.is_valid or geom.is_empty or not bounds.covers(geom):
                raise ValueError('Invalid/out-of-cell geometry: '+feature['id'])
            if feature['id'] in unique:
                old_geom = shape(unique[feature['id']]['geometry'])
                if (geom.area, geom.length) > (old_geom.area, old_geom.length):
                    unique[feature['id']] = feature
                removed += 1
            else:
                unique[feature['id']] = feature
        bus_ids = {feature['properties']['source_record_id'] for feature in unique.values() if feature['properties']['kind'] == 'bus_stop'}
        redundant_platforms = [key for key, feature in unique.items() if feature['properties']['kind'] == 'platform' and feature['properties']['source_record_id'] in bus_ids]
        for key in redundant_platforms:
            del unique[key]
            removed += 1
        for feature in unique.values():
            if asset['detail_level'] == 'detail':
                props = feature['properties']
                key = props['source_record_id']+':'+props['kind']
                if key not in originals:
                    originals[key] = True
                    kinds[props['kind']] += 1
        if len(unique) != len(collection['features']):
            collection['features'] = list(unique.values())
            atomic_json(path, collection)
            asset.update(count=len(unique), sha256=digest(path), bytes=path.stat().st_size)
    report.update(fragment_count=sum(a['count'] for a in report['assets']), bytes=sum(a['bytes'] for a in report['assets']), source_feature_counts_unique=dict(kinds), duplicate_representations_removed=removed, staged_geometry_validation='passed')
    if source_path is not None and source_path.exists() and digest(source_path) == report['source_sha256']:
        with osmium.io.Reader(str(source_path), osmium.osm.NOTHING) as reader:
            report['source_snapshot_at'] = reader.header().get('osmosis_replication_timestamp') or None
    atomic_json(manifest, report)
    return {key:report[key] for key in ('asset_count','fragment_count','bytes','source_feature_counts_unique','duplicate_representations_removed','staged_geometry_validation')}


def build_search_index(manifest: Path | None = None, limit: int = 50000) -> dict:
    """Index named facilities without inventing geocoded positions or addresses."""
    if limit < 1 or limit > 50000:
        raise ValueError('Search index limit must be 1..50000')
    manifest = manifest or LOCAL/'audit'/'infrastructure-korea.json'
    report = json.loads(manifest.read_text(encoding='utf-8'))
    priority = {'settlement':0, 'airport':1, 'port':2, 'ferry_terminal':3, 'station':4, 'bus_station':5, 'industrial_land':6, 'airport_terminal':7, 'pier':8, 'bus_stop':9, 'platform':10}
    candidates = {}
    for asset in report['assets']:
        if asset['detail_level'] != 'detail':
            continue
        path = PUBLIC/asset['url'].removeprefix('/data/')
        if digest(path) != asset['sha256']:
            raise ValueError('Staged infrastructure checksum mismatch')
        collection = json.loads(path.read_text(encoding='utf-8'))
        for feature in collection['features']:
            props = feature['properties']
            kind, name = props['kind'], props.get('name', '').strip()
            if kind not in priority or not name:
                continue
            key = props['source_record_id']+':'+kind
            geom = shape(feature['geometry'])
            rank = geom.area if geom.geom_type in ('Polygon', 'MultiPolygon') else geom.length
            if key in candidates and candidates[key][0] >= rank:
                continue
            point = geom.interpolate(.5, normalized=True) if geom.geom_type in ('LineString', 'MultiLineString') else geom.representative_point()
            entry = {'id':'osm-'+key, 'name':name, 'alt_names':[props['name:en']] if props.get('name:en') else [], 'kind':kind, 'kind_label':LABELS[kind], 'lon':point.x, 'lat':point.y, 'bbox':list(geom.bounds), 'source_id':'osm', 'source_record_id':props['source_record_id'], 'source_url':props['source_url'], 'position_evidence':'source_attribute' if geom.geom_type == 'Point' else 'calculation', 'position_method':'original_node' if geom.geom_type == 'Point' else 'representative_point_of_largest_cell_fragment', 'range':25000 if kind == 'settlement' else 4000}
            candidates[key] = (rank, entry)
    entries = sorted((value[1] for value in candidates.values()), key=lambda item:(priority[item['kind']], item['name'], item['id']))[:limit]
    index = {'schema_version':1, 'source_sha256':report['source_sha256'], 'source_snapshot_at':report.get('source_snapshot_at'), 'transform_version':VERSION, 'candidate_count':len(candidates), 'entry_count':len(entries), 'omitted_count':max(0, len(candidates)-limit), 'entries':entries}
    filename = 'search-index.json' if limit == 50000 else f'search-index-{limit}.json'
    path = PUBLIC/'infrastructure'/f'{report["source_sha256"][:12]}-{VERSION}'/filename
    atomic_json(path, index)
    result = {'url':'/data/'+path.relative_to(PUBLIC).as_posix(), 'sha256':digest(path), 'bytes':path.stat().st_size, 'count':len(entries), 'candidate_count':len(candidates), 'omitted_count':index['omitted_count'], 'counts_by_kind':dict(Counter(item['kind'] for item in entries))}
    atomic_json(LOCAL/'audit'/'infrastructure-search.json', result)
    return compact_search_index(manifest,path)


def compact_search_index(manifest: Path | None = None, index_path: Path | None = None) -> dict:
    """Move common provenance out of every record before serving a public search index."""
    manifest = manifest or LOCAL/'audit'/'infrastructure-korea.json'
    report = json.loads(manifest.read_text(encoding='utf-8'))
    path = index_path or PUBLIC/'infrastructure'/f'{report["source_sha256"][:12]}-{VERSION}'/'search-index.json'
    index = json.loads(path.read_text(encoding='utf-8'))
    if not index.get('compact'):
        atomic_json(LOCAL/'silver'/f'infrastructure-search-full-{report["source_sha256"][:12]}-{path.stem}.json',index)
        keys = ('id','name','alt_names','kind','kind_label','lon','lat','range','position_evidence')
        entries = [{key:entry[key] for key in keys if key in entry and (key != 'alt_names' or entry[key])} for entry in index['entries']]
        index = {**{key:index[key] for key in ('schema_version','candidate_count','entry_count','omitted_count')}, 'compact':True, 'source_id':'osm', 'source_url':report['source_url'], 'source_snapshot_at':report.get('source_snapshot_at'), 'source_sha256':report['source_sha256'], 'transform_version':VERSION, 'entries':entries}
        atomic_json(path,index)
    result = {'url':'/data/'+path.relative_to(PUBLIC).as_posix(), 'sha256':digest(path), 'bytes':path.stat().st_size, 'count':index['entry_count'], 'candidate_count':index['candidate_count'], 'omitted_count':index['omitted_count'], 'counts_by_kind':dict(Counter(entry['kind'] for entry in index['entries'])), 'compact':True}
    atomic_json(LOCAL/'audit'/'infrastructure-search.json',result)
    return result


def split_large_detail(manifest: Path | None = None, max_bytes: int = 4*1024*1024) -> dict:
    """Reduce dense-city overfetch while preserving every source geometry fragment."""
    manifest = manifest or LOCAL/'audit'/'infrastructure-korea.json'
    report = json.loads(manifest.read_text(encoding='utf-8'))
    assets = []
    replaced = 0
    for asset in report['assets']:
        if asset['detail_level'] != 'detail' or asset['bytes'] <= max_bytes or asset.get('grid_degrees') == .01:
            assets.append(asset)
            continue
        path = PUBLIC/asset['url'].removeprefix('/data/')
        if digest(path) != asset['sha256']:
            raise ValueError('Detail source checksum mismatch')
        collection = json.loads(path.read_text(encoding='utf-8'))
        size = .01 if asset.get('grid_degrees') == .05 else .05
        grouped = {}
        for feature in collection['features']:
            props = feature['properties']
            for x, y, fragment in cell_parts(shape(feature['geometry']), size):
                key = (x, y)
                grouped.setdefault(key, []).append({**feature, 'id':f'{props["source_record_id"]}:{props["kind"]}:fine:{size}:{x}:{y}', 'geometry':mapping(fragment)})
        for (x, y), features in sorted(grouped.items()):
            filename = path.parent/f'{path.stem}-fine-{x}-{y}.geojson'
            bbox = [x*size,y*size,(x+1)*size,(y+1)*size]
            envelope = box(*bbox).buffer(1e-10)
            ids = set()
            for feature in features:
                geometry = shape(feature['geometry'])
                if geometry.is_empty or not geometry.is_valid or not envelope.covers(geometry) or feature['id'] in ids:
                    raise ValueError('Invalid refined detail geometry/ID')
                ids.add(feature['id'])
            atomic_json(filename, {'type':'FeatureCollection', 'features':features})
            assets.append({**asset, 'id':f'{asset["id"]}-fine-{x}-{y}', 'url':'/data/'+filename.relative_to(PUBLIC).as_posix(), 'bbox':bbox, 'count':len(features), 'sha256':digest(filename), 'bytes':filename.stat().st_size, 'grid_degrees':size})
        replaced += 1
    report.update(assets=assets, asset_count=len(assets), fragment_count=sum(asset['count'] for asset in assets), bytes=sum(asset['bytes'] for asset in assets), coarse_detail_cells_refined=replaced)
    atomic_json(manifest, report)
    return {'coarse_detail_cells_refined':replaced, 'asset_count':len(assets), 'max_detail_bytes':max((asset['bytes'] for asset in assets if asset['detail_level']=='detail'), default=0)}


def partition_overview(manifest: Path | None = None, max_bytes: int = 4*1024*1024) -> dict:
    """Keep nationwide views from loading city-level roads and dense facility polygons."""
    manifest = manifest or LOCAL/'audit'/'infrastructure-korea.json'
    report = json.loads(manifest.read_text(encoding='utf-8'))
    if report.get('overview_partitioned'):
        return {'already_partitioned':True, 'asset_count':report['asset_count']}
    assets = []
    dense = {'industrial_land','station','bus_station'}

    def write_group(asset, path, group, features, bbox, suffix):
        collection = {'type':'FeatureCollection','features':features}
        output = path.parent/f'{path.stem}-{group}-{suffix}.geojson'
        # Validate the new pieces directly; unchanged detail assets were already audited.
        envelope = box(*bbox).buffer(1e-10)
        ids = set()
        for feature in features:
            geometry = shape(feature['geometry'])
            if geometry.is_empty or not geometry.is_valid or not envelope.covers(geometry) or feature['id'] in ids:
                raise ValueError('Invalid refined overview geometry/ID')
            ids.add(feature['id'])
        atomic_json(output, collection)
        assets.append({**asset, 'id':f'{asset["id"]}-{group}-{suffix}', 'url':'/data/'+output.relative_to(PUBLIC).as_posix(), 'bbox':bbox, 'count':len(features), 'sha256':digest(output), 'bytes':output.stat().st_size, 'max_camera_height':1500000 if group == 'sparse' else 350000, 'overview_group':group})

    def partition(features, size):
        chunks = {}
        for feature in features:
            props = feature['properties']
            for x,y,geometry in cell_parts(shape(feature['geometry']),size):
                chunks.setdefault((x,y),[]).append({**feature, 'id':f'{props["source_record_id"]}:{props["kind"]}:overview:{size}:{x}:{y}', 'geometry':mapping(geometry)})
        return chunks

    for asset in report['assets']:
        if asset['detail_level'] != 'overview':
            assets.append(asset)
            continue
        path = PUBLIC/asset['url'].removeprefix('/data/')
        if digest(path) != asset['sha256']:
            raise ValueError('Overview source checksum mismatch')
        grouped = {}
        for feature in json.loads(path.read_text(encoding='utf-8'))['features']:
            kind = feature['properties']['kind']
            group = 'roads' if kind == 'road' else 'dense' if kind in dense else 'sparse'
            grouped.setdefault(group,[]).append(feature)
        for group,features in sorted(grouped.items()):
            payload_bytes = len(json.dumps(features,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
            if payload_bytes <= max_bytes:
                write_group(asset,path,group,features,asset['bbox'],'whole')
                continue
            for (x,y),chunk in sorted(partition(features,.25).items()):
                chunk_bytes = len(json.dumps(chunk,ensure_ascii=False,separators=(',',':')).encode('utf-8'))
                if chunk_bytes <= max_bytes:
                    write_group(asset,path,group,chunk,[x*.25,y*.25,(x+1)*.25,(y+1)*.25],f'{x}-{y}')
                else:
                    for (fine_x,fine_y),fine in sorted(partition(chunk,.05).items()):
                        write_group(asset,path,group,fine,[fine_x*.05,fine_y*.05,(fine_x+1)*.05,(fine_y+1)*.05],f'{x}-{y}-fine-{fine_x}-{fine_y}')
    report.update(assets=assets, asset_count=len(assets), bytes=sum(asset['bytes'] for asset in assets), fragment_count=sum(asset['count'] for asset in assets), overview_partitioned=True)
    atomic_json(manifest,report)
    result = {'asset_count':len(assets), 'bytes':report['bytes'], 'max_asset_bytes':max(asset['bytes'] for asset in assets), 'max_overview_bytes':max(asset['bytes'] for asset in assets if asset['detail_level']=='overview')}
    atomic_json(LOCAL/'audit'/'infrastructure-overview.json',result)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('pbf', type=Path)
    source = parser.parse_args().pbf
    extract_infrastructure(source)
    print(json.dumps(validate_staged(source_path=source), ensure_ascii=False))
    print(json.dumps(split_large_detail(), ensure_ascii=False))
    print(json.dumps(split_large_detail(), ensure_ascii=False))
    print(json.dumps(validate_staged(source_path=source), ensure_ascii=False))
    print(json.dumps(partition_overview(), ensure_ascii=False))
    print(json.dumps(build_search_index(), ensure_ascii=False))
