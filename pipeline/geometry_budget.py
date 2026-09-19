"""Measure GeoJSON work budgets from immutable source bytes before publication."""
from __future__ import annotations

import hashlib
import json

from .core import PUBLIC
from .retile import vertex_count


def verify_geometry_budgets(assets, base=PUBLIC, *, fill_missing=False):
    """Reject incorrect declarations; optionally fill only absent budget fields."""
    base = base.resolve()
    rows, changed = [], []
    for asset in assets:
        if asset['format'] != 'geojson':
            continue
        if not asset['url'].startswith('/data/'):
            raise ValueError('GeoJSON is not a local immutable asset: '+asset['id'])
        path = (base/asset['url'].removeprefix('/data/')).resolve()
        if not path.is_relative_to(base):
            raise ValueError('GeoJSON path escapes public data')
        payload = path.read_bytes()
        if hashlib.sha256(payload).hexdigest() != asset['sha256']:
            raise ValueError('GeoJSON hash mismatch: '+asset['id'])
        document = json.loads(payload)
        if document.get('type') != 'FeatureCollection' or not isinstance(document.get('features'), list):
            raise ValueError('Expected GeoJSON FeatureCollection: '+asset['id'])
        actual = {'byte_length': len(payload), 'feature_count': len(document['features']),
                  'vertex_count': sum(vertex_count(f['geometry']) if f.get('geometry') else 0
                                      for f in document['features'])}
        missing = []
        for key, value in actual.items():
            if key not in asset:
                if not fill_missing:
                    raise ValueError('Missing GeoJSON work budget '+key+': '+asset['id'])
                asset[key] = value
                missing.append(key)
            elif type(asset[key]) is not int or asset[key] != value:
                raise ValueError('Incorrect GeoJSON work budget '+key+': '+asset['id'])
        rows.append({'id': asset['id'], 'sha256': asset['sha256'], **actual})
        if missing:
            changed.append({'id': asset['id'], 'filled': missing, **actual})
    return {'passed': True, 'geojson_assets_checked': len(rows), 'filled': changed,
            'total_features': sum(r['feature_count'] for r in rows),
            'total_vertices': sum(r['vertex_count'] for r in rows),
            'total_bytes': sum(r['byte_length'] for r in rows), 'assets': rows}
