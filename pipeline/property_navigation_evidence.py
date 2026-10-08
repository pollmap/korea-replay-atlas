"""Confirm existing address-linked points against the provider's public map.

This confirms the provider's navigation marker only. It neither establishes a
parcel/entrance nor assigns a CRS to the original OpenAptInfo coordinate fields.
No new identity is joined here: only the audited release's address links qualify.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

from .property_navigation import build as build_navigation
from .seoul_apartments import canonical

SOURCE_URL = 'https://openapt.seoul.go.kr/commonPortal/programLink.do?jspNm=/portal/aMenu/aptInfo/aptInfo.open'
METHOD = 'official_site_marker_same_kapt_code_and_coordinates'
IDENTITY = 'unique_official_road_address_and_name'
MARKER = re.compile(r'onclick="fn_moveMap\(this,\s*\'(A\d{8,12})\',\s*\'([0-9.]+)\',\s*\'([0-9.]+)\'\)"')
NAVIGATION = re.compile(r'map\.setView\(\[\s*y\s*,\s*x\s*\],\s*15\s*\)')


def build(point_bytes: bytes, page_bytes: bytes, marker_bytes: bytes, checked_at: str):
    if not 0 < len(page_bytes) <= 1024**2 or not 0 < len(marker_bytes) <= 8 * 1024**2:
        raise ValueError('provider_evidence_size_invalid')
    if not checked_at.endswith('Z'):
        raise ValueError('evidence_timestamp_invalid')
    datetime.fromisoformat(checked_at.replace('Z', '+00:00'))
    page, html = page_bytes.decode('utf-8'), marker_bytes.decode('utf-8')
    if not NAVIGATION.search(page) or 'function fn_moveMap(target, aptCode, x, y)' not in page:
        raise ValueError('provider_navigation_contract_changed')
    index, manifest = build_navigation(point_bytes)
    source = json.loads(index)
    markers = {}
    for code, x, y in MARKER.findall(html):
        if code in markers:
            raise ValueError('provider_marker_duplicate_identity')
        lon, lat = float(x), float(y)
        if not 126.6 <= lon <= 127.4 or not 37.3 <= lat <= 37.8:
            raise ValueError('provider_marker_coordinate_invalid')
        markers[code] = (lon, lat)
    if not markers:
        raise ValueError('provider_markers_missing')
    confirmed, mismatched, absent = [], [], []
    for row in source['points']:
        identity, code, lon, lat = row
        if code not in markers:
            absent.append(identity)
        elif markers[code] != (lon, lat):
            mismatched.append(identity)
        else:
            confirmed.append(row)
    if not confirmed:
        raise ValueError('no_confirmed_navigation_points')
    result = {'schema_version': 1, 'property_release_id': manifest['release_id'],
              'source_sha256': manifest['source_sha256'], 'identity_rule': IDENTITY,
              'method': METHOD, 'point_semantics': 'provider_map_navigation_marker',
              'checked_at': checked_at, 'source_url': SOURCE_URL,
              'navigation_page_sha256': hashlib.sha256(page_bytes).hexdigest(),
              'marker_response_sha256': hashlib.sha256(marker_bytes).hexdigest(),
              'points': confirmed}
    encoded = canonical(result) + b'\n'
    meta = {'release_id': manifest['release_id'], 'source_sha256': manifest['source_sha256'],
            'sha256': hashlib.sha256(encoded).hexdigest(), 'bytes': len(encoded)}
    audit = {'linked': len(source['points']), 'confirmed': len(confirmed),
             'coordinate_mismatch': mismatched, 'missing_provider_id': absent,
             'provider_markers': len(markers)}
    return encoded, meta, audit
