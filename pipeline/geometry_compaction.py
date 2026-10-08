"""Lossless 2D GeoJSON compaction and spatial work budgets."""
import hashlib
import json
import math
import sqlite3
from shapely.geometry import shape
from .core import PUBLIC, digest

MAX_JSON = 3 * 1024 * 1024
MINIMAL = ('source_record_id', 'kind', 'name', 'highway', 'railway', 'building', 'height', 'height_method', 'quality_state', 'render_eligible')

def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()


def compact_features(features):
    """Losslessly factor common properties; never quantize geometry coordinates."""
    if not features:
        return {'type': 'FeatureCollection', 'features': [], 'metadata': {'schema_version': 1, 'shared': {}, 'rows': []}}
    shared = dict(features[0].get('properties') or {})
    for feature in features[1:]:
        props = feature.get('properties') or {}
        shared = {k: v for k, v in shared.items() if k in props and encoded(props[k]) == encoded(v)}
    rows, result = [], []
    for feature in features:
        props = feature.get('properties') or {}
        minimal = {k: props[k] for k in MINIMAL if k in props}
        rest = {k: v for k, v in props.items() if k not in shared and k not in minimal}
        # Empty rows can be shared; all other rows remain deterministic.
        index = len(rows)
        rows.append(rest)
        result.append({**feature, 'properties': {**minimal, 'metadata_index': index}})
    return {'type': 'FeatureCollection', 'features': result, 'metadata': {'schema_version': 1, 'shared': shared, 'rows': rows}}


def restore_features(document):
    metadata = document['metadata']
    return [{**f, 'properties': {**metadata['shared'], **metadata['rows'][f['properties']['metadata_index']],
                               **{k: v for k, v in f['properties'].items() if k != 'metadata_index'}}}
            for f in document['features']]


def bounds_of(features):
    bounds = [shape(f['geometry']).bounds for f in features]
    return [min(b[0] for b in bounds), min(b[1] for b in bounds), max(b[2] for b in bounds), max(b[3] for b in bounds)]


def vertex_count(geometry):
    if geometry['type'] == 'GeometryCollection':
        return sum(vertex_count(part) for part in geometry['geometries'])
    def walk(values):
        if not values:
            return 0
        if isinstance(values[0], (int, float)):
            return 1
        return sum(walk(value) for value in values)
    return walk(geometry['coordinates'])


def feature_position(feature):
    b = shape(feature['geometry']).bounds
    return (b[0] + b[2]) / 2, (b[1] + b[3]) / 2


def cell_key(feature, dx=.05, dy=None):
    x, y = feature_position(feature)
    return math.floor(x / dx + 1e-8), math.floor(y / (dy or dx) + 1e-8)


def emit_compact(features, folder, stem, template, maximum=MAX_JSON):
    document = compact_features(features)
    payload = encoded(document)
    if len(payload) > maximum and len(features) > 1:
        # Source order is often an OSM edit history, not geographic order. Its
        # two halves can each span an entire city, forcing the client to load
        # both even for a small view. Partition whole features on the widest
        # geographic axis; retain crossing geometry and every original vertex.
        positioned = [(feature_position(feature), feature) for feature in features]
        west = min(point[0] for point, _ in positioned)
        east = max(point[0] for point, _ in positioned)
        south = min(point[1] for point, _ in positioned)
        north = max(point[1] for point, _ in positioned)
        axis = 0 if (east-west)*math.cos(math.radians((south+north)/2)) >= north-south else 1
        ordered = [feature for _, feature in sorted(positioned, key=lambda item: (
            item[0][axis], item[0][1-axis], str(item[1].get('id', ''))))]
        middle = len(ordered) // 2
        return emit_compact(ordered[:middle], folder, stem+'a', template, maximum) + emit_compact(ordered[middle:], folder, stem+'b', template, maximum)
    if len(payload) >= 24 * 1024 * 1024:
        raise ValueError('Individual GeoJSON feature exceeds asset limit')
    if restore_features(document) != features:
        raise ValueError('Compact GeoJSON lossless round-trip failed')
    sha = hashlib.sha256(payload).hexdigest()
    path = folder / f'{stem}-{sha[:16]}.geojson'
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(payload)
    return [{**template, 'version': template.get('version', template.get('dataset_version', 'source-records')), 'id': stem, 'url': '/data/' + path.relative_to(PUBLIC).as_posix(), 'bytes': len(payload), 'byte_length': len(payload),
             'sha256': sha, 'count': len(features), 'bbox': bounds_of(features), 'format': 'geojson',
             'feature_count': len(features), 'vertex_count': sum(vertex_count(f['geometry']) for f in features),
             'geometry_encoding': 'geojson-metadata-table-v1'}]


def enrich_geometry_budgets(assets):
    """Complete work budgets for resumed descriptors from older checkpoints."""
    for asset in assets:
        if not asset.get('version'):
            version = asset.get('dataset_version')
            if not isinstance(version, str) or not version:
                raise ValueError('Asset has no explicit source version: '+asset['id'])
            asset['version'] = version
        if asset['format'] != 'geojson' or 'vertex_count' in asset:
            continue
        path = PUBLIC/asset['url'].removeprefix('/data/')
        if digest(path) != asset['sha256']:
            raise ValueError('Geometry changed before recording work budget')
        document = json.loads(path.read_bytes())
        asset.update(feature_count=len(document['features']), vertex_count=sum(vertex_count(f['geometry']) for f in document['features']),
                     byte_length=path.stat().st_size)


def build_geometry(catalog, output, work):
    """Spool spatial groups on disk; bound RAM to one source/tile group."""
    database = work/'geometry.sqlite'
    db = sqlite3.connect(database)
    db.execute('CREATE TABLE IF NOT EXISTS records (source TEXT, ordinal INTEGER, grp TEXT, payload TEXT, PRIMARY KEY(source,ordinal))')
    db.execute('CREATE TABLE IF NOT EXISTS complete (source TEXT PRIMARY KEY, sha TEXT)')
    # Preserve the station-only role so the UI can suppress duplicate station
    # points while the infrastructure layer is active.
    db.execute("UPDATE records SET grp=replace(grp,'rail-detail-osm|','rail-stations-detail-osm|') WHERE source='osm-stations-korea' AND grp LIKE 'rail-detail-osm|%'")
    db.commit()
    templates, inputs = {}, []
    for asset in catalog['assets']:
        if asset['layer'] not in ('infrastructure', 'rail') or asset['format'] != 'geojson':
            continue
        level = asset.get('detail_level', 'detail')
        source = asset.get('source_id', 'osm')
        role = '-stations' if asset['id'] == 'osm-stations-korea' else ''
        prefix = f'{asset["layer"]}{role}-{level}-{source}'
        templates[prefix] = {k: asset[k] for k in ('layer', 'source_id', 'dataset_version', 'detail_level', 'min_camera_height', 'max_camera_height', 'evidence_type') if k in asset}
        templates[prefix]['version'] = 'source-records'
        if role:
            templates[prefix]['geometry_role'] = 'stations'
        inputs.append(asset)
        prior = db.execute('SELECT sha FROM complete WHERE source=?', (asset['id'],)).fetchone()
        if prior and prior[0] == asset['sha256']:
            continue
        path = PUBLIC/asset['url'].removeprefix('/data/')
        if digest(path) != asset['sha256']:
            raise ValueError('Geometry source hash mismatch: '+asset['id'])
        document = json.loads(path.read_text(encoding='utf-8'))
        features = document['features']
        if len(features) != asset['count']:
            raise ValueError('Geometry source count mismatch')
        db.execute('DELETE FROM records WHERE source=?', (asset['id'],))
        for index, feature in enumerate(features):
            grid = .5 if level == 'overview' else .25
            key = cell_key(feature, grid)
            group = f'{prefix}|{key[0]}|{key[1]}'
            db.execute('INSERT INTO records VALUES (?,?,?,?)', (asset['id'], index, group, encoded(feature).decode()))
        db.execute('INSERT OR REPLACE INTO complete VALUES (?,?)', (asset['id'], asset['sha256']))
        db.commit()
    db.execute('CREATE INDEX IF NOT EXISTS spatial_group ON records(grp)')
    groups = [row[0] for row in db.execute('SELECT DISTINCT grp FROM records ORDER BY grp')]
    result, count = [], 0
    for index, group in enumerate(groups):
        prefix, x, y = group.split('|')
        template = templates[prefix]
        # Cap uncompressed source metadata in memory; compact output gets a
        # separate hard size check. Every original ID and vertex is retained.
        batch, size, page = [], 0, 0
        for (payload,) in db.execute('SELECT payload FROM records WHERE grp=? ORDER BY source,ordinal', (group,)):
            batch.append(json.loads(payload)); size += len(payload)
            if size >= 8*1024*1024:
                result.extend(emit_compact(batch, output/'geometry', f'{prefix}-{x}-{y}-{page}', template))
                count += len(batch); batch, size, page = [], 0, page+1
        if batch:
            result.extend(emit_compact(batch, output/'geometry', f'{prefix}-{x}-{y}-{page}', template)); count += len(batch)
        if index % 50 == 0:
            print(json.dumps({'stage': 'retile-geometry', 'groups': index+1, 'total': len(groups), 'files': len(result)}), flush=True)
    db.close()
    expected = sum(a['count'] for a in inputs)
    if count != expected:
        raise ValueError('Geometry coverage mismatch')
    for asset in result:
        if asset['layer'] == 'rail':
            asset['detail_level'] = 'detail'
            asset['max_camera_height'] = min(asset.get('max_camera_height', 200000), 200000)
    overview = build_rail_overview(catalog, output)
    result.extend(overview)
    return result, {'source_files': len(inputs), 'output_files': len(result), 'feature_count': count, 'source_geometry_error': 0,
                    'geometry_error_scope': 'unchanged detail geometry; overview is explicitly generalized',
                    'overview_files': len(overview), 'overview_feature_count': sum(a['count'] for a in overview), 'overview_tolerance_m': 100,
                    'metadata_roundtrip': 'all output features compared before serialization', 'source_sha256': {a['id']: a['sha256'] for a in inputs}}


def build_rail_overview(catalog, output):
    from pyproj import Transformer
    from shapely.geometry import mapping
    from shapely.ops import transform
    source = next((asset for asset in catalog['assets'] if asset['id'] == 'osm-rail-korea'), None)
    if source is None:
        return []
    path = PUBLIC/source['url'].removeprefix('/data/')
    if digest(path) != source['sha256']:
        raise ValueError('Rail overview source hash mismatch')
    document = json.loads(path.read_bytes())
    metric = Transformer.from_crs(4326, 5179, always_xy=True).transform
    geographic = Transformer.from_crs(5179, 4326, always_xy=True).transform
    features = []
    for original in document['features']:
        generalized = transform(geographic, transform(metric, shape(original['geometry'])).simplify(100, preserve_topology=True))
        if generalized.is_empty:
            raise ValueError('Rail overview simplification lost a source feature')
        feature = {**original, 'geometry': mapping(generalized), 'properties': {**original.get('properties', {}),
                   'geometry_precision': 'generalized_for_distant_view', 'simplification_tolerance_m': 100,
                   'lod_role': 'overview_generalized'}}
        # Round-trip tuples into standard JSON arrays before metadata comparison.
        features.append(json.loads(encoded(feature)))
    assets = emit_compact(features, output/'geometry', 'rail-overview-generalized-osm', {
        'layer': 'rail', 'source_id': source['source_id'], 'version': source['version'], 'detail_level': 'overview',
        'min_camera_height': 200000, 'geometry_role': 'generalized', 'generalization_m': 100})
    if sum(asset['count'] for asset in assets) != source['count']:
        raise ValueError('Rail overview source identity count changed')
    return assets
