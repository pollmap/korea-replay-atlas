"""Build a small, release-pinned navigation index from audited provider points.

This does not establish coordinate accuracy or match new identities. Only the
one-to-one identities already established by property_point_join are indexed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re

from .seoul_apartments import canonical


def build(body: bytes):
    if not 0 < len(body) <= 2 * 1024**2:
        raise ValueError('point_size_invalid')
    data = json.loads(body)
    release = data.get('metadata', {}).get('property_release_id', '')
    if (not re.fullmatch(r'property-[a-f0-9]{16}', release)
            or data.get('type') != 'FeatureCollection'
            or not isinstance(data.get('features'), list)
            or len(data['features']) > 3000):
        raise ValueError('point_contract_invalid')
    points, ids, codes = [], set(), set()
    for feature in data['features']:
        props = feature.get('properties', {})
        identity = props.get('property_complex_id')
        if identity is None:
            continue
        code = props.get('kapt_code')
        xy = feature.get('geometry', {}).get('coordinates')
        if (not isinstance(identity, str) or not re.fullmatch(r'molit-apt:11\d{3}:[A-Za-z0-9_-]{1,64}', identity)
                or not isinstance(code, str) or not re.fullmatch(r'A\d{8,12}', code)
                or identity in ids or code in codes
                or props.get('property_release_id') != release
                or props.get('property_aptseq_join') != 'unique_official_road_address_and_name'
                or props.get('coordinate_status') != 'provider_xy_crs_unconfirmed'
                or feature.get('geometry', {}).get('type') != 'Point'
                or not isinstance(xy, list) or len(xy) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in xy)
                or not 126.6 <= xy[0] <= 127.4 or not 37.3 <= xy[1] <= 37.8):
            raise ValueError('point_identity_or_coordinate_invalid')
        ids.add(identity)
        codes.add(code)
        points.append([identity, code, *xy])
    if not points:
        raise ValueError('no_linked_points')
    source_sha = hashlib.sha256(body).hexdigest()
    index = {'schema_version': 1, 'property_release_id': release,
             'coordinate_status': 'provider_xy_crs_unconfirmed', 'source_sha256': source_sha,
             'points': sorted(points)}
    encoded = canonical(index) + b'\n'
    manifest = {'release_id': release, 'sha256': hashlib.sha256(encoded).hexdigest(),
                'bytes': len(encoded), 'source_sha256': source_sha}
    return encoded, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--points', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    args = parser.parse_args()
    body, manifest = build(args.points.read_bytes())
    for target, content in ((args.output, body), (args.manifest, canonical(manifest) + b'\n')):
        if target.exists() and target.read_bytes() != content:
            raise ValueError('immutable_output_changed')
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            target.write_bytes(content)
    print(json.dumps({'release_id': manifest['release_id'], 'points': len(json.loads(body)['points']),
                      'bytes': len(body), 'sha256': manifest['sha256']}))


if __name__ == '__main__':
    main()
