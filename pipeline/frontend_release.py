"""Restage a frontend against an unchanged, previously audited static release.

This deliberately does not call ``static_release.prepare``: the prior bundle is
the only source of data files, including every catalog and observation list.
``static_release.stage(reuse_bundle=...)`` still verifies the prior receipt,
configuration, manifest, Worker and *all payload bytes* before materializing a
new bundle. Its existing hardlink and 30 GiB reserve protections remain active.

Only existing Vite chunk families, index.html and download-gate.js may change,
plus explicitly audited point/navigation, SGIS selection and pinned OSM POI assets.
New chunk families require full staging unless their exact audited bytes are
listed below. Copied-library, Worker or deployment-policy changes still require
full staging. No credentials are read to perform this operation;
the frontend scan rejects recognizable credential literals, but cannot prove
that an arbitrary unlabelled string is not a secret.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from pathlib import Path

from . import static_release as release
from .core import LOCAL, ROOT, digest, now
from .recovery_bundle import (
    broker_deployment_contract, bundle_file_plan, no_links, read_json,
    regular_file, safe_relative, tree_files,
)

GENERATED = frozenset(('data/catalog.json', '_headers', '404.html'))
MUTABLE_ROOT = frozenset(('index.html', 'download-gate.js'))
ROOT_FILES = MUTABLE_ROOT | {'.assetsignore'}
CHUNK_NAME = re.compile(r'assets/([A-Za-z0-9_][A-Za-z0-9_.-]*)-[A-Za-z0-9_-]{8}\.(js|css)\Z')
# The PC price loader is shared by Map2D and PropertyExplorer, so Rollup emits
# one additional module. Approve this source-audited build, not arbitrary JS or
# a mutable new chunk family. Every existing data/vendor/Worker byte stays pinned.
AUDITED_REPLACED_CHUNKS = {
    ('property-summary-client', 'js'): {
        'sha256': '7ed320f4b06c19a801af6a9903f40c748b0e9eec1be29c249855398d1231c09e',
        'bytes': 12559, 'replacement': ('property-view', 'js'),
    },
}
AUDITED_ADDITIONAL_CHUNKS = {
    ('property-search.worker', 'js'): {'sha256': 'cdc6fb9b123acb7ae859683a4fa28e584c16178020d73ff2e4c6126caef1545f', 'bytes': 3080},
    ('property-view', 'js'): {
        'sha256': 'ebe9402c9cdd0e92ca9f1fe44f6b1bd8ec7fa911049f9843e6bf103adb480411',
        'bytes': 12566,
    },
    ('property-summary-client', 'js'): {
        'sha256': '121589dbdb3da2561d15703c2e1512a8dcfb9746e33f84cb4c0df75609e0c397',
        'bytes': 12559,
    },
}
SEOUL_KAPT_GEOJSON_SHA = '8360eb2d88be0ab4259b5d92e5a98d25372e6bf19ad739dfbad6c26622debe82'
SEOUL_KAPT_JOINED_SHA = '33058dae0a1d86c302b2f1c5b0dff9d71241a60031880f4f738c9fe506611792'
SEOUL_KAPT_RECENT_SHA = 'b63b62af834062de98b142f4caef4f8a2087bd8e713c15ba3351fcf3dc06859c'
SEOUL_KAPT_ASSET_HASHES = {
    'ceeff63959643461': '6caa09e8f851aedb84251d41404fc325e14009a812dd0fdfabe686eab50b58e1',
    'b87eea7c1c03dc21': '3ec8394b76c8e2328008bf734c673d203f58514403666c807f6a6f4096a32b6c',
    '8360eb2d88be0ab4': SEOUL_KAPT_GEOJSON_SHA,
    '33058dae0a1d86c3': SEOUL_KAPT_JOINED_SHA,
    'b63b62af834062de': SEOUL_KAPT_RECENT_SHA,
    '14916a799c24a49e': '14916a799c24a49e0bd2c91311dfc8e98ff287fb4ce8d09a958bad75f1208c35',
    '7843533a17275616': '7843533a172756165fbe4c8ad9e5eb479915b349d8d2d91de47a7a38476f6187',
    '8deba5b9951e48da': 'd017162bdd00a6b9f8f124d971e5c8c84d8dedb929cb782186e7df66b0ced8e7',
    '8879dff1b31ac5f0': '55ad17067ec49bfc73085da633cd0c9b3735495ed94db1735002510e1fb21344',
}
SEOUL_KAPT_GEOJSON = re.compile(r'assets/seoul-kapt-points-([a-f0-9]{16})-[A-Za-z0-9_-]{8}\.geojson\Z')
PROPERTY_NAVIGATION = re.compile(r'assets/seoul-property-navigation-(?:[a-f0-9]{16}-)?[A-Za-z0-9_-]{8}\.json\Z')
PROPERTY_NAVIGATION_SHA = 'b93cbc63ea3e74836f349ed11dc73742ee2095f9ba258a13b9812c726879cf7e'
PROPERTY_NAVIGATION_ASSETS = {
    ('856ef8862b77887ad1eec0c2f42e77a0c46e0adcfcf27498ed05a1ab3fefb859', 54875),
    ('2f345f225c735627b1adf5da4ac9388385d9e806fcddb514db2f41b9eeaa7851', 55666),
    (PROPERTY_NAVIGATION_SHA, 55929),
    ('9ee094b267838e30d2f5117c027444d16fedc8b5348f1214011a265e93432e7c', 55930),
    ('7d1758f5552a4e0d0d20119f97813624b0bad9d30f5684cca245910580132112', 55930),
    ('4f0987966a5ea103cb4f91b1ef938fc8350640192e9f0c4ed0ad2c95d9863037', 55929),
    ('009c22e31c65d6c5def9f821bd3dab6a0083b433866c5d2ee612d78f85f6e36d', 55666),
}
PROPERTY_METRIC_ASSET = re.compile(r'assets/(property-(?:region-metrics|revisions|search-index)-[a-f0-9]{16}|[0-9]{5})-[A-Za-z0-9_-]{8}\.json\Z')
# Exact row-audited source bytes; this never accepts arbitrary frontend JSON.
PROPERTY_METRIC_ASSETS = {'11110': {'sha256': '0f910ced0ccf4e895aa31598cb6c0f8b4b58b26ed1444ffae483a6f173f9b9e8', 'bytes': 56424}, '11140': {'sha256': '628e64de16db75b1f85f52c4c9ddc42942178dc37507e26540633bac8553f82a', 'bytes': 61020}, '11170': {'sha256': '321d4123c076b431aea4a088e9ff2da6ba1f76e8f28950528e91537de0bbedca', 'bytes': 73257}, '11200': {'sha256': '783ba4f0f7a9fd10ada1b949ea350352f9e3a7e57ba5f5775c1a672c60d0abad', 'bytes': 110342}, '11215': {'sha256': '3b5ef86cf774111bf644fc1438b9e319b0ead91d0ccd78dd25163b0be953d617', 'bytes': 67367}, '11230': {'sha256': '087a0553dc007e3ad835cb2e899fc7ef77d4947e37f2fe7b50a5d67919eebd64', 'bytes': 117089}, '11260': {'sha256': '1a4179e4c4db9882ae793d5db7ad244f3ec4491549212856426e684b482fd58e', 'bytes': 75389}, '11290': {'sha256': '3115e48f1662edc2769f3731b69a80a9e65baa10506ec66bb1a9cff136d9826e', 'bytes': 100626}, '11305': {'sha256': '17e6f4ffdcc81ae726dc2a6d7f5b20a8ada96da70bd6f6b33fbf37b94ceb14c6', 'bytes': 60035}, '11320': {'sha256': '49e7e8c05672a520c6bf958426c559e9a25bc9413e2fa598b64cf756b7d4d7da', 'bytes': 55388}, '11350': {'sha256': 'a455577844d574d5643af8d0cc24624abc5d831731fce3b0dbcc53e6de822f8d', 'bytes': 90145}, '11380': {'sha256': '0e478c5cc15a55133e2cd4072d13197429fd8b6f4f2d4d768ddd14fe98492272', 'bytes': 141518}, '11410': {'sha256': '1663ab04d58d1ec6f60a041d329c160fe8cdeaf6ef2b0eca76e7fc509e9afd4b', 'bytes': 98065}, '11440': {'sha256': '984e83705253e698f0bc852edbae7fede6ba8903d57e7cdd2f947da8843d213f', 'bytes': 135747}, '11470': {'sha256': '5bfa5a4816726d50e2f0cc17c17b61c8e6a10315f7bf31754bd5c6670cafd06b', 'bytes': 64463}, '11500': {'sha256': '1171c8b87680834436cca5544f8a8a4bb2949279ce14f8a4808cff031a19f0fc', 'bytes': 140132}, '11530': {'sha256': 'bcd78479c130801feb47a21ec3bb2f1fce575c8d7c87c9915203db621dd36a14', 'bytes': 91756}, '11545': {'sha256': '4a49bea75ca24d97cb112d7c1ef77525524ce4af189ae9c84d251817275fe5f7', 'bytes': 59801}, '11560': {'sha256': 'e54e6c6aeb73ff8db39346daf328b99af1ba373cc8647f44fb3c2d3cfbd40b7a', 'bytes': 127280}, '11590': {'sha256': '1a1ef5ef2e3c0cb6c2371d7284236adcc1a797366c4100a48f7ae1a12970eeb7', 'bytes': 115027}, '11620': {'sha256': 'cb4dd0d6195e7234147cb8629ad134c7cf1d67adf89edf7d2658f70c683603f8', 'bytes': 68399}, '11650': {'sha256': 'd9a55ea809b7e4a1da8d921d7f840b78a3285403d19fab291ebb9c41bae291b6', 'bytes': 182319}, '11680': {'sha256': 'f05065712619217e9d59220e8ef9c4e83abcb5e8655b7a7d7922ddc3891c471e', 'bytes': 156985}, '11710': {'sha256': '7658ab28be068fb232cf1a821d56b74ed7b7fb380a17b33badbc3038f3fdd35b', 'bytes': 129636}, '11740': {'sha256': 'b7f5431224885b79a3e3ab92eabe2b7cd9c097e177cddaa48033a5388e1b08df', 'bytes': 121964}, 'property-region-metrics-8879dff1b31ac5f0': {'sha256': '592713f558bf0224951d650e778ebd502d318e80a704f5fd7d277041086cdc73', 'bytes': 4000582}, 'property-region-metrics-b87eea7c1c03dc21': {'sha256': 'c7af492a53c95678e2f7fb6a7fbf7bcd33d8e9a3c7b96096f9f116a5f5195b67', 'bytes': 4008029}}

PROPERTY_METRIC_ASSETS['property-revisions-ceeff63959643461'] = {'sha256': '8e0cc98a0e025729f20468339f7fad1907240105598094d60929680ecaa4b2b3', 'bytes': 847285}

PROPERTY_METRIC_ASSETS['property-region-metrics-ceeff63959643461'] = {'sha256': '9133576d08d0b1c2f08812e874a8f780625f21fbf5f477e306661b1d6115d35a', 'bytes': 4014876}

PROPERTY_METRIC_ASSETS['property-search-index-ceeff63959643461'] = {'sha256': '5472abd27fa4eb3c223892eab17a44397428873c7bccb37bb65e8a30dc42114c', 'bytes': 2423276}

def _approved_metric_asset(name, sha, size):
    match = PROPERTY_METRIC_ASSET.fullmatch(name)
    return bool(match and PROPERTY_METRIC_ASSETS.get(match[1]) == {'sha256': sha, 'bytes': size})

REGION_ASSET = re.compile(r'assets/(boundary-(?:[0-9]{2}|[0-9]{5})|dongs-[0-9]{5})-[A-Za-z0-9_-]{8}\.json\Z')
REGION_SOURCE_ROOT = Path(__file__).resolve().parents[1] / 'src/data'
REGION_ARCHIVE_SHA = 'f1cf0f9de453ac7eaacb273f39cee52851183372b9ddfda428a967c3a670b2c6'
REGION_REFERENCE_DATE = '2025-06-30'
REGION_MAX_FILE_BYTES = 1024 * 1024
REGION_MAX_TOTAL_BYTES = 10 * 1024 * 1024
REGION_MAX_FILES = 521
POI_ASSET = re.compile(r'assets/poi-([a-f0-9]{64})-[A-Za-z0-9_-]{8}\.json\Z')
POI_SOURCE_ROOT = Path(__file__).resolve().parents[1] / 'src/data/property-poi'
POI_MAX_FILE_BYTES = 1024 * 1024
POI_MAX_TOTAL_BYTES = 32 * 1024 * 1024
POI_MAX_FILES = 1000
POI_SOURCE = {
    'id': 'osm', 'label': 'OpenStreetMap contributors',
    'url': 'https://download.geofabrik.de/asia/south-korea-latest.osm.pbf',
    'asOf': '2026-09-15T20:20:37Z',
    'sha256': '3135b6ec7b3d94294735de0aa47d49a58c06b638bf33ba44e30b5728cc5b76c7',
    'license': 'ODbL-1.0',
}
POI_TYPES = {'transport': {'subway', 'rail', 'bus'},
             'school': {'elementary', 'middle', 'high', 'university', 'school'},
             'life': {'shopping', 'medical', 'park', 'public'}}
POI_SCOPE_CODES = frozenset(('11', '23', '31', '25', '29', '21', '34011', '34012', '34040',
                             '33041', '33042', '33043', '33044'))
POI_SCOPE_METADATA = {
    'boundaryDate': REGION_REFERENCE_DATE, 'boundaryNamespace': 'SGIS administrative',
    'boundaryManifestSha256': '0ab0a840ae7ed45347f8e057a23236ff51cc27527b3714f886aa334afcdef41a',
    'boundaryArchiveSha256': REGION_ARCHIVE_SHA, 'maximumDisplayErrorMetres': 20.0069,
    'coverageComplete': False,
}
POI_TAG_KEYS = frozenset(('amenity', 'healthcare', 'leisure', 'shop', 'railway', 'station',
                        'public_transport', 'highway', 'bus', 'subway', 'train',
                        'isced:level', 'office', 'operator', 'ref'))


def _poi_json(body):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate POI JSON property')
            result[key] = value
        return result

    def invalid(_):
        raise ValueError('Invalid POI JSON number')

    return json.loads(body, object_pairs_hook=unique, parse_constant=invalid)


def _poi_asset_inventory():
    """An exact canonical source inventory, never a dist-provided allowlist."""
    index = POI_SOURCE_ROOT / 'manifest.json'
    if regular_file(index).st_size > POI_MAX_FILE_BYTES:
        raise ValueError('POI manifest exceeds size budget')
    manifest_body = index.read_bytes()
    if len(manifest_body) > POI_MAX_FILE_BYTES:
        raise ValueError('POI manifest exceeds size budget')
    if any(pattern.search(manifest_body) for pattern in CREDENTIAL_PATTERNS):
        raise ValueError('Credential-like content is forbidden in POI manifest')
    manifest = _poi_json(manifest_body)
    if not isinstance(manifest, dict):
        raise ValueError('Invalid POI source manifest')
    scope = manifest.get('scope', {})
    if (set(manifest) != {'schema', 'source', 'scope', 'chunks'}
            or type(manifest.get('schema')) is not int or manifest['schema'] != 1
            or manifest.get('source') != POI_SOURCE or not isinstance(scope, dict)
            or set(scope) != set(POI_SCOPE_METADATA) | {'regionCodes'}
            or {key: scope.get(key) for key in POI_SCOPE_METADATA} != POI_SCOPE_METADATA
            or scope.get('coverageComplete') is not False
            or not isinstance(scope.get('regionCodes'), list) or not scope['regionCodes']
            or any(not isinstance(code, str) for code in scope['regionCodes'])
            or set(scope['regionCodes']) != POI_SCOPE_CODES
            or len(set(scope['regionCodes'])) != len(scope['regionCodes'])
            or not isinstance(manifest.get('chunks'), list) or not 0 < len(manifest['chunks']) <= POI_MAX_FILES):
        raise ValueError('POI source revision, scope or inventory is not audited')
    result, keys, ids = {}, set(), set()
    total_bytes = 0
    for ref in manifest['chunks']:
        if (not isinstance(ref, dict) or set(ref) != {'key', 'west', 'south', 'east', 'north', 'file', 'sha256', 'bytes', 'count'}
                or not isinstance(ref.get('key'), str) or len(ref['key']) > 80
                or not re.fullmatch(r'[0-9]{1,4}-[0-9]{1,4}(?:-(?:00|01|10|11|a|b))*', ref['key'])
                or ref['key'] in keys or type(ref.get('bytes')) is not int or not 0 < ref['bytes'] <= POI_MAX_FILE_BYTES
                or type(ref.get('count')) is not int or not 0 < ref['count'] <= 10000
                or not isinstance(ref.get('sha256'), str) or not re.fullmatch(r'[a-f0-9]{64}', ref['sha256'])
                or ref.get('file') != 'poi-' + ref['sha256'] + '.json' or ref['sha256'] in result):
            raise ValueError('Invalid POI chunk identity or byte budget')
        keys.add(ref['key'])
        bounds = [ref[k] for k in ('west', 'south', 'east', 'north')]
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in bounds)
                or not 123 <= bounds[0] < bounds[2] <= 133 or not 32 <= bounds[1] < bounds[3] <= 40):
            raise ValueError('Invalid POI chunk bounds')
        path = POI_SOURCE_ROOT / ref['file']
        if regular_file(path).st_size != ref['bytes']:
            raise ValueError('POI source size differs from audited manifest')
        body = path.read_bytes()
        if len(body) != ref['bytes'] or hashlib.sha256(body).hexdigest() != ref['sha256']:
            raise ValueError('POI source hash differs from audited manifest')
        if any(pattern.search(body) for pattern in CREDENTIAL_PATTERNS):
            raise ValueError('Credential-like content is forbidden in POI assets')
        payload = _poi_json(body)
        if (not isinstance(payload, dict) or set(payload) != {'schema', 'records'}
                or type(payload.get('schema')) is not int or payload['schema'] != 1
                or not isinstance(payload.get('records'), list) or len(payload['records']) != ref['count']):
            raise ValueError('Invalid POI payload schema or record count')
        for row in payload['records']:
            required = {'id', 'name', 'category', 'type', 'longitude', 'latitude', 'positionMethod'}
            if (not isinstance(row, dict) or not required.issubset(row)
                    or set(row) - required - {'address', 'sourceTags', 'scopeRegionCode', 'nameIsFallback'}
                    or not isinstance(row.get('id'), str) or not re.fullmatch(r'(?:node|way|relation)/[1-9][0-9]{0,19}', row['id'])
                    or row['id'] in ids or not isinstance(row.get('category'), str) or row['category'] not in POI_TYPES
                    or not isinstance(row.get('type'), str) or row['type'] not in POI_TYPES[row['category']]
                    or not isinstance(row.get('positionMethod'), str)
                    or row['positionMethod'] not in {'original_node', 'area_representative_point', 'line_midpoint'}
                    or ('nameIsFallback' in row and row['nameIsFallback'] is not True)
                    or row['id'].startswith('node/') != (row['positionMethod'] == 'original_node')):
                raise ValueError('Invalid or duplicate POI source identity or classification')
            for field in ('name', 'address'):
                if field in row and (not isinstance(row[field], str) or not row[field].strip() or len(row[field]) > 500
                                     or any(ord(char) < 32 for char in row[field])):
                    raise ValueError('Invalid POI display text')
            lon, lat = row['longitude'], row['latitude']
            if (type(lon) not in (int, float) or type(lat) not in (int, float)
                    or not math.isfinite(lon) or not math.isfinite(lat)
                    or not bounds[0] <= lon <= bounds[2] or not bounds[1] <= lat <= bounds[3]):
                raise ValueError('POI coordinates exceed the declared chunk')
            if 'scopeRegionCode' in row and row['scopeRegionCode'] not in scope['regionCodes']:
                raise ValueError('POI record region differs from audited scope')
            if 'sourceTags' in row and (not isinstance(row['sourceTags'], dict) or len(row['sourceTags']) > len(POI_TAG_KEYS)
                    or any(k not in POI_TAG_KEYS
                           or not isinstance(v, str) or not v or len(v) > 300
                           or any(ord(c) < 32 for c in v) for k, v in row['sourceTags'].items())):
                raise ValueError('Invalid POI source tags')
            ids.add(row['id'])
        result[ref['sha256']] = {'bytes': ref['bytes'], 'sha256': ref['sha256']}
        total_bytes += ref['bytes']
        if total_bytes > POI_MAX_TOTAL_BYTES:
            raise ValueError('POI inventory exceeds total byte budget')
    return result


def _region_asset_inventory():
    """Trust only the canonical audited source manifests, never dist manifests.

    Geometry is source-audited separately. This gate pins every published byte,
    administrative code, feature count and source revision to that source tree.
    No user-supplied manifest path or frontend option can widen this inventory.
    """
    result = {}
    total_bytes = 0
    for stem, prefix, directory, expected_chunks, expected_features in (
        ('region-selection', 'boundary', 'region-selection-areas', 269, 269),
        ('region-selection-dongs', 'dongs', 'region-selection-dongs', 252, 3559),
    ):
        index = REGION_SOURCE_ROOT / (stem + '.json')
        if regular_file(index).st_size > REGION_MAX_FILE_BYTES:
            raise ValueError('Region source manifest exceeds size budget')
        manifest = read_json(index)
        if (manifest.get('schema') != 1 or manifest.get('referenceDate') != REGION_REFERENCE_DATE
                or manifest.get('namespace') != 'SGIS administrative'
                or manifest.get('archiveSha256') != REGION_ARCHIVE_SHA
                or manifest.get('featureCount') != expected_features
                or not isinstance(manifest.get('chunks'), list)
                or len(manifest['chunks']) != expected_chunks):
            raise ValueError('Region source manifest revision or count is not audited')
        feature_ids = set()
        for row in manifest['chunks']:
            if not isinstance(row, list) or len(row) != 3:
                raise ValueError('Invalid region source manifest reference')
            code, size, sha = row
            pattern = r'(?:[0-9]{2}|[0-9]{5})' if prefix == 'boundary' else r'[0-9]{5}'
            if (not isinstance(code, str) or not re.fullmatch(pattern, code)
                    or type(size) is not int or not 0 < size <= REGION_MAX_FILE_BYTES
                    or not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha)):
                raise ValueError('Invalid region source identity or byte budget')
            key = prefix + '-' + code
            if key in result:
                raise ValueError('Duplicate region source identity')
            source = REGION_SOURCE_ROOT / directory / (key + '.json')
            if regular_file(source).st_size != size:
                raise ValueError('Region source size differs from audited manifest')
            body = source.read_bytes()
            if len(body) != size or hashlib.sha256(body).hexdigest() != sha:
                raise ValueError('Region source hash differs from audited manifest')
            if any(pattern.search(body) for pattern in CREDENTIAL_PATTERNS):
                raise ValueError('Credential-like content is forbidden in region assets')
            features = json.loads(body)
            if not isinstance(features, list) or not features or prefix == 'boundary' and len(features) != 1:
                raise ValueError('Invalid region source feature count')
            for feature in features:
                if (not isinstance(feature, list) or len(feature) != 4
                        or not isinstance(feature[0], str) or not isinstance(feature[1], str)
                        or type(feature[2]) is not int or not 0 <= feature[2] <= 9
                        or not isinstance(feature[3], list) or not feature[3]
                        or (feature[0] != code if prefix == 'boundary' else not re.fullmatch(code + r'[0-9]{3}', feature[0]))
                        or feature[0] in feature_ids):
                    raise ValueError('Invalid or duplicate region source feature identity')
                feature_ids.add(feature[0])
            result[key] = {'bytes': size, 'sha256': sha}
            total_bytes += size
        if len(feature_ids) != expected_features:
            raise ValueError('Region source feature count differs from manifest')
    if len(result) != REGION_MAX_FILES or total_bytes > REGION_MAX_TOTAL_BYTES:
        raise ValueError('Region source inventory exceeds audited count or byte budget')
    return result


def _approved_point_asset(name: str, sha: str, size: int) -> bool:
    match = SEOUL_KAPT_GEOJSON.fullmatch(name)
    return bool(match and SEOUL_KAPT_ASSET_HASHES.get(match[1]) == sha and size <= 2 * 1024 * 1024
                or PROPERTY_NAVIGATION.fullmatch(name) and (sha, size) in PROPERTY_NAVIGATION_ASSETS)
FORBIDDEN_NAME = re.compile(r'(?i)(?:^|[-_.])(?:secrets?|credentials?|tokens?|private|id_rsa)(?:$|[-_.])')
CREDENTIAL_PATTERNS = (
    re.compile(rb'-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----'),
    re.compile(rb'\b(?:github_pat_[A-Za-z0-9_]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|AKIA[A-Z0-9]{16})\b'),
    re.compile(rb'''(?ix)(?:DATA_GO_KR_SERVICE_KEY|SEOUL_SUBWAY_API_KEY|CLOUDFLARE_API_TOKEN|
        QA_TOKEN|oauth_token|service_?key|auth_?key|api_?key|access_token|client_secret)
        ["']?\s*[:=]\s*["']?([A-Za-z0-9+/%=_-]{12,})'''),
    re.compile(rb'''(?i)\bAuthorization["']?\s*[:=]\s*["']?Bearer\s+[A-Za-z0-9._~+/-]{12,}'''),
)


def _inside(root: Path, path: Path, description: str) -> Path:
    value = Path(path)
    if '..' in value.parts:
        raise ValueError(description + ' must not traverse outside its directory')
    value = value if value.is_absolute() else root / value
    no_links(value)
    value = value.resolve()
    if value == root or not value.is_relative_to(root):
        raise ValueError(description + ' must stay inside the workspace')
    return value


def _family(name: str):
    match = CHUNK_NAME.fullmatch(name)
    return match.groups() if match else None


def _frontend_name(name: str) -> None:
    safe_relative(name)
    if name == '.assetsignore':
        return
    if (any(part.startswith('.') for part in name.split('/'))
            or FORBIDDEN_NAME.search(Path(name).name)):
        raise ValueError('Private or hidden files are forbidden in the frontend')
    if name not in MUTABLE_ROOT and not name.startswith('cesium/') and not _family(name) and not SEOUL_KAPT_GEOJSON.fullmatch(name) and not PROPERTY_NAVIGATION.fullmatch(name) and not REGION_ASSET.fullmatch(name) and not POI_ASSET.fullmatch(name) and not PROPERTY_METRIC_ASSET.fullmatch(name):
        raise ValueError('Unknown frontend file; full staging is required')


def _frontend_entries(client: Path, previous: dict):
    """Closed path inventory, unchanged vendor files, and bounded text scanning."""
    old = {name: entry for name, entry in previous.items()
           if not name.startswith('data/') and name not in GENERATED}
    if 'index.html' not in old:
        raise ValueError('Previous frontend has no index.html')
    families = set()
    directories = {'assets'}
    for name in old:
        _frontend_name(name)
        family = _family(name)
        if family:
            if family in families:
                raise ValueError('Ambiguous previous frontend chunk family')
            families.add(family)
        directories.update(parent.as_posix() for parent in Path(name).parents if parent.as_posix() != '.')

    # tree_files rejects links, junctions, sockets and nonregular files. Check
    # directories too so an empty data/ or hidden directory cannot slip through.
    paths = sorted(tree_files(client))
    region_inventory = _region_asset_inventory() if any(REGION_ASSET.fullmatch(path.relative_to(client).as_posix()) for path in paths) else {}
    poi_required = (POI_SOURCE_ROOT / 'manifest.json').exists() or any(POI_ASSET.fullmatch(name) for name in old) or any(POI_ASSET.fullmatch(path.relative_to(client).as_posix()) for path in paths)
    poi_inventory = _poi_asset_inventory() if poi_required else {}
    seen_pois = set()
    seen_regions = set()
    pending = [client]
    while pending:
        folder = pending.pop()
        for child in folder.iterdir():
            no_links(child)
            if child.is_dir():
                if child.relative_to(client).as_posix() not in directories:
                    raise ValueError('Unknown frontend directory; full staging is required')
                pending.append(child)
    result = []
    names = set()
    seen_families = set()
    additional_families = set()
    for path in paths:
        name = path.relative_to(client).as_posix()
        _frontend_name(name)
        if name.casefold() in names:
            raise ValueError('Duplicate frontend path')
        names.add(name.casefold())
        family = _family(name)
        if family:
            if (family not in families and family not in AUDITED_ADDITIONAL_CHUNKS
                    or family in seen_families):
                raise ValueError('Changed frontend chunk families; full staging is required')
            if family not in families:
                additional_families.add(family)
            seen_families.add(family)
        elif name not in old and not SEOUL_KAPT_GEOJSON.fullmatch(name) and not PROPERTY_NAVIGATION.fullmatch(name) and not REGION_ASSET.fullmatch(name) and not POI_ASSET.fullmatch(name) and not PROPERTY_METRIC_ASSET.fullmatch(name):
            raise ValueError('Unknown frontend file; full staging is required')
        size = regular_file(path).st_size
        if not 0 <= size < release.MAX_FILE_BYTES:
            raise ValueError('Frontend file exceeds the static asset size ceiling')
        sha = digest(path)
        if family in additional_families and AUDITED_ADDITIONAL_CHUNKS[family] != {'sha256': sha, 'bytes': size}:
            raise ValueError('Additional frontend module differs from audited bytes')
        prior = old.get(name)
        unchanged = prior and prior['sha256'] == sha and prior['bytes'] == size
        approved_point_asset = _approved_point_asset(name, sha, size)
        metric_match = PROPERTY_METRIC_ASSET.fullmatch(name)
        approved_metric_asset = _approved_metric_asset(name, sha, size)
        if metric_match and not approved_metric_asset:
            raise ValueError('Property metric asset differs from audited bytes')
        region_match = REGION_ASSET.fullmatch(name)
        approved_region_asset = bool(region_match and region_inventory.get(region_match[1]) == {'bytes': size, 'sha256': sha})
        if region_match:
            if not approved_region_asset:
                raise ValueError('Region asset is unlisted or differs from audited manifest')
            if region_match[1] in seen_regions:
                raise ValueError('Duplicate published region asset identity')
            seen_regions.add(region_match[1])
        poi_match = POI_ASSET.fullmatch(name)
        approved_poi_asset = bool(poi_match and poi_inventory.get(poi_match[1]) == {'bytes': size, 'sha256': sha})
        if poi_match:
            if not approved_poi_asset:
                raise ValueError('POI asset is unlisted or differs from audited manifest')
            if poi_match[1] in seen_pois:
                raise ValueError('Duplicate published POI asset identity')
            seen_pois.add(poi_match[1])
        if name not in MUTABLE_ROOT and not family and not unchanged and not approved_point_asset and not approved_region_asset and not approved_poi_asset and not approved_metric_asset:
            raise ValueError('Copied frontend assets changed; full staging is required')
        if (family or region_match or poi_match or metric_match) and prior and not unchanged:
            raise ValueError('An immutable frontend asset URL changed bytes')
        if name in MUTABLE_ROOT or family or approved_point_asset or approved_region_asset or approved_poi_asset or approved_metric_asset:
            # Reads are bounded by the same 24 MiB ceiling as static staging.
            body = path.read_bytes()
            if len(body) != size or hashlib.sha256(body).hexdigest() != sha:
                raise ValueError('Frontend changed during inspection')
            if b'\x00' in body:
                raise ValueError('Frontend text contains binary content')
            try:
                body.decode('utf-8')
            except UnicodeDecodeError:
                raise ValueError('Frontend text is not UTF-8') from None
            if any(pattern.search(body) for pattern in CREDENTIAL_PATTERNS):
                raise ValueError('Credential-like content is forbidden in the frontend')
        result.append({'path': str(path), 'target': name, 'bytes': size, 'sha256': sha})
    # Immutable app previews retain their former URL. A replaced, SHA-checked
    # point or region asset need not be copied into the next app bundle.
    stable_names = set(old) - {name for name, entry in old.items()
                               if _family(name) or _approved_point_asset(name, entry['sha256'], entry['bytes'])
                               or _approved_metric_asset(name, entry['sha256'], entry['bytes'])
                               or REGION_ASSET.fullmatch(name) and REGION_ASSET.fullmatch(name)[1] in seen_regions
                               or POI_ASSET.fullmatch(name) and poi_inventory}
    if seen_pois != set(poi_inventory):
        raise ValueError('Audited POI assets are missing from the frontend')
    if seen_regions != set(region_inventory):
        raise ValueError('Audited region assets are missing from the frontend')
    replaced_families = set()
    for family in families - seen_families:
        rule = AUDITED_REPLACED_CHUNKS.get(family)
        members = [entry for name, entry in old.items() if _family(name) == family]
        if (rule and rule['replacement'] in additional_families and len(members) == 1
                and all(members[0][key] == rule[key] for key in ('sha256', 'bytes'))):
            replaced_families.add(family)
    if not stable_names.issubset({entry['target'] for entry in result}) or seen_families != (families - replaced_families) | additional_families:
        raise ValueError('Frontend files or chunk families are missing; full staging is required')
    return result


def _verify_current_worker(worker: Path, receipt: dict) -> None:
    expected = {entry['target']: entry['sha256'] for entry in receipt['worker_files']}
    actual = {}
    for path in tree_files(worker):
        name = path.relative_to(worker).as_posix()
        safe_relative(name)
        # These Vite outputs are excluded by static_release.stage as well. Do
        # not open .dev.vars: recognizing its name is not permission to read it.
        if name in ('.dev.vars', 'wrangler.json') or name.startswith('.vite/'):
            continue
        if (any(part.startswith('.') for part in name.split('/'))
                or path.suffix not in ('.js', '.mjs', '.cjs', '.wasm', '.map')):
            raise ValueError('Unknown Worker build file; full staging is required')
        target = 'worker/' + name
        if target.casefold() in {value.casefold() for value in actual}:
            raise ValueError('Duplicate Worker build path')
        actual[target] = digest(path)
    if actual != expected:
        raise ValueError('Worker build changed; full staging is required')


def _prior_metadata(bundle: Path):
    # This reads small metadata and the catalog only. The existing stage function
    # performs full byte verification exactly through its normal reuse path.
    receipt, _ = bundle_file_plan(bundle)
    if (Path(receipt.get('bundle', '')).absolute() != bundle
            or Path(receipt.get('config', '')).absolute() != bundle / 'wrangler.json'
            or bundle.name != receipt['bundle_id']):
        raise ValueError('Previous receipt does not identify this immutable bundle')
    config = read_json(bundle / 'wrangler.json')
    if (receipt.get('schema_version') != 2
            or receipt.get('deployment_contract') != broker_deployment_contract()
            or config != release.deployment_config(receipt['release_id'])):
        raise ValueError('Deployment configuration changed; full staging is required')
    entries = read_json(bundle / 'asset-manifest.json')
    assets = {entry['target']: entry for entry in entries}
    if len(assets) != len(entries) or not GENERATED.issubset(assets):
        raise ValueError('Previous manifest has invalid generated files')
    files = [entry for entry in entries if entry['target'] not in GENERATED]
    headers = release.headers_for(files)
    for name, body in (('_headers', headers.encode()), ('404.html', release.NOT_FOUND.encode())):
        entry = assets[name]
        if entry['bytes'] != len(body) or entry['sha256'] != hashlib.sha256(body).hexdigest():
            raise ValueError('Generated deployment policy changed; full staging is required')
    fingerprint = hashlib.sha256(release.encoded({
        'assets': [(entry['target'], entry['sha256']) for entry in files],
        'worker': [(entry['target'], entry['sha256']) for entry in receipt['worker_files']],
        'catalog': receipt['catalog_hash'], 'headers': headers,
        'not_found': release.NOT_FOUND, 'config_version': 3,
        'config': config, 'deployment_contract': receipt['deployment_contract'],
    })).hexdigest()[:16]
    if fingerprint != receipt['bundle_id']:
        raise ValueError('Previous bundle fingerprint does not match its manifest')
    release_catalog = assets.get('data/releases/' + receipt['release_id'] + '.json')
    if not release_catalog or release_catalog['sha256'] != receipt['catalog_hash']:
        raise ValueError('Previous immutable catalog is not pinned to the receipt')
    return receipt, assets


def restage(*, reuse_bundle: Path, client_dir: Path | None = None,
            worker_dir: Path | None = None, output: Path | None = None,
            root: Path = ROOT):
    """Create a new frontend-only bundle; never regenerate or mutate map data."""
    no_links(root)
    root = Path(root).resolve()
    bundle = _inside(root, reuse_bundle, 'Reuse bundle')
    client = _inside(root, client_dir or root / 'dist/client', 'Frontend build')
    worker = _inside(root, worker_dir or root / 'dist/korea_replay', 'Worker build')
    output = _inside(root, output or root / '.local/deploy', 'Staging output')
    if (not client.is_relative_to(root / 'dist') or not worker.is_relative_to(root / 'dist')
            or not bundle.is_relative_to(root / '.local') or not output.is_relative_to(root / '.local')
            or output == bundle or output.is_relative_to(bundle)
            or client == worker or client.is_relative_to(worker) or worker.is_relative_to(client)):
        raise ValueError('Frontend-only inputs must use distinct dist and local bundle directories')
    receipt, assets = _prior_metadata(bundle)
    receipt_hash = digest(bundle / 'receipt.json')
    _verify_current_worker(worker, receipt)
    frontend = _frontend_entries(client, assets)
    data = [
        {'path': str(bundle / 'client' / name), 'target': name,
         'bytes': entry['bytes'], 'sha256': entry['sha256']}
        for name, entry in assets.items()
        if name.startswith('data/') and name not in GENERATED
    ]
    files = data + frontend
    if len(files) + len(GENERATED) > release.MAX_FILES:
        raise ValueError('Frontend-only bundle exceeds the static file count ceiling')
    plan = {
        'schema_version': 1, 'mode': 'static', 'release_id': receipt['release_id'], 'created_at': now(),
        'catalog_path': str(bundle / 'client/data/catalog.json'), 'catalog_hash': receipt['catalog_hash'],
        'count': len(files) + len(GENERATED), 'bytes': sum(entry['bytes'] for entry in files), 'files': files,
        'audit': {'passed': True, 'release_id': receipt['release_id'],
                  'scope': 'Frontend-only; inherited audited data, with full static-stage byte verification'},
        'frontend_only': {
            'schema_version': 1, 'reuse_bundle_id': receipt['bundle_id'], 'receipt_hash': receipt_hash,
            'manifest_hash': receipt['manifest_hash'], 'config_hash': receipt['config_hash'],
            'source_catalog_hash': receipt['catalog_hash'], 'immutable_data_files': len(data) + 1,
            'immutable_data_bytes': sum(entry['bytes'] for entry in data) + assets['data/catalog.json']['bytes'],
        },
    }
    if digest(bundle / 'receipt.json') != receipt_hash:
        raise ValueError('Previous receipt changed during frontend inspection')
    _verify_current_worker(worker, receipt)
    # Pin actual staged code to the verified prior Worker. A concurrent Vite build
    # cannot inject a different Worker between our check and stage's materializer.
    return release.stage(plan, output=output, worker_dir=bundle / 'worker', reuse_bundle=bundle)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-bundle', type=Path, required=True,
                        help='Complete immutable staged bundle; all original bytes are still verified')
    parser.add_argument('--client-dir', type=Path, default=ROOT / 'dist/client')
    parser.add_argument('--worker-dir', type=Path, default=ROOT / 'dist/korea_replay')
    parser.add_argument('--output', type=Path, default=LOCAL / 'deploy')
    args = parser.parse_args(argv)
    try:
        result = restage(reuse_bundle=args.reuse_bundle, client_dir=args.client_dir,
                         worker_dir=args.worker_dir, output=args.output)
    except (OSError, ValueError) as error:
        # Never include payload bytes or scanner matches in diagnostic output.
        parser.exit(2, 'Frontend-only staging refused: ' + str(error) + '\n')
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    main()
