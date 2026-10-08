"""Join official station depth and position records with explicit matching rules."""
import csv
import io
import json
import math
import re
import requests
from pathlib import Path
from collections import defaultdict
from .core import LOCAL,atomic_json,digest,now

def csv_rows(path):
    data=path.read_bytes()
    for encoding in ('utf-8-sig','cp949'):
        try:text=data.decode(encoding);break
        except UnicodeDecodeError:continue
    else:raise ValueError('Unknown CSV encoding')
    if '<html' in text[:200].lower():raise ValueError('CSV source returned an HTML error')
    return list(csv.DictReader(io.StringIO(text)))

def seoul_file(dataset,sequence,file_sequence,name):
    path=LOCAL/'raw'/'seoul'/name
    if path.exists() and path.with_suffix('.meta.json').exists():
        csv_rows(path)
        return path
    url='https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false'
    response=requests.post(url,data={'infId':dataset,'seq':sequence,'infSeq':file_sequence},timeout=45)
    response.raise_for_status()
    if b'<html' in response.content[:200].lower():raise ValueError('Seoul file download rejected')
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(response.content)
    atomic_json(path.with_suffix('.meta.json'),{'source_url':f'https://data.seoul.go.kr/dataList/{dataset}/F/1/datasetView.do','sha256':digest(path),'retrieved_at':now(),'sequence':sequence,'file_sequence':file_sequence})
    return path

ALIASES={'을지3가':'을지로3가','을지4가':'을지로4가'}

VERTICAL_SOURCE_URL = 'https://data.seoul.go.kr/dataList/OA-13305/F/1/datasetView.do'
VERTICAL_VERSION = 'seoul-depth-source-elevations-1'


def station_vertical_evidence(row):
    """Retain official +100.3m elevations without assuming a geoid datum.

    Arithmetic consistency is testable; the dataset does not identify the
    surveyed vertical CRS needed to transform its absolute heights to WGS84.
    Never replace terrain heights merely because the offset is documented.
    """
    values = {}
    flags = ['official_absolute_vertical_datum_unverified']
    for key, field in [('ground', '지반고'), ('rail', '레일면고'),
                       ('rail_depth', '선로기준정거장깊이'), ('platform_depth', '정거장깊이')]:
        try:
            value = float(row[field])
            if not math.isfinite(value): raise ValueError()
            values[key] = value
        except (KeyError, TypeError, ValueError):
            values[key] = None
            flags.append('missing_or_invalid_' + key)
    all_values = all(value is not None for value in values.values())
    residual = (values['ground'] - values['rail'] - values['rail_depth']) if all_values else None
    platform_residual = (values['rail_depth'] - 1.1 - values['platform_depth']) if all_values else None
    if residual is not None and abs(residual) > .06:
        flags.append('official_ground_rail_depth_inconsistent')
    if platform_residual is not None and abs(platform_residual) > .06:
        flags.append('official_platform_rail_depth_inconsistent')
    return {'vertical_evidence_version': VERTICAL_VERSION,
        'official_ground_level_raw_m': values['ground'], 'official_rail_level_raw_m': values['rail'],
        'official_level_offset_m': 100.3,
        'official_ground_elevation_m': values['ground'] - 100.3 if values['ground'] is not None else None,
        'official_rail_elevation_m': values['rail'] - 100.3 if values['rail'] is not None else None,
        'official_vertical_datum': 'source mean sea level; exact realization unverified',
        'absolute_vertical_datum_verified': False,
        'official_ground_rail_residual_m': residual,
        'official_platform_rail_residual_m': platform_residual,
        'position_method': 'terrain_minus_official_platform_depth',
        'vertical_quality_flags': flags, 'vertical_definition_url': VERTICAL_SOURCE_URL}
def join_key(line,name):
    # No fuzzy name or proximity joins across lines.
    name=re.sub(r'\s+','',name)
    return str(line).strip(),ALIASES.get(name,name)

def depth():
    from .retired_3d import retired
    retired()


def review_vertical(root=None):
    """Audit existing official files and prepare enriched features privately."""
    import copy
    import hashlib
    import sqlite3
    from collections import Counter
    from .core import ROOT
    root = Path(root or ROOT)
    raw = root / '.local/raw/seoul/depth-20241104.csv'
    positions = root / '.local/raw/seoul/coordinates-20250814.csv'
    rows = csv_rows(raw)
    evidence = [station_vertical_evidence(row) for row in rows]
    counts = Counter(flag for item in evidence for flag in item['vertical_quality_flags'])
    coordinate_index = defaultdict(list)
    for row in csv_rows(positions):
        coordinate_index[join_key(row['호선'], row['역명'])].append(row)
    by_id = {}
    for row, item in zip(rows, evidence):
        matches = coordinate_index[join_key(row['호선'], row['역명'])]
        if len(matches) == 1:
            by_id[f'{row["호선"]}:{matches[0]["고유역번호(외부역코드)"]}'] = item
    with sqlite3.connect((root / '.local/catalog.sqlite').resolve().as_uri() + '?mode=ro', uri=True) as db:
        asset = json.loads(db.execute('SELECT payload FROM assets WHERE id=?', ('depth-seoul',)).fetchone()[0])
    path = root / 'public' / asset['url'].lstrip('/')
    if digest(path) != asset['sha256']:
        raise ValueError('Published source depth hash mismatch')
    original = json.loads(path.read_text(encoding='utf-8'))
    enriched = copy.deepcopy(original)
    matched = 0
    for feature in enriched['features']:
        item = by_id.get(feature['id'])
        if item:
            feature['properties'].update(item)
            matched += 1
    key = hashlib.sha256((digest(raw) + digest(positions) + asset['sha256'] + VERTICAL_VERSION).encode()).hexdigest()[:16]
    output = root / '.local/review/depth-vertical' / f'seoul-{key}.geojson'
    atomic_json(output, enriched)
    report = {'version': VERTICAL_VERSION, 'source_rows': len(rows), 'source_sha256': digest(raw),
        'coordinate_sha256': digest(positions), 'source_public_asset_sha256': asset['sha256'],
        'source_definition_url': VERTICAL_SOURCE_URL, 'flags': dict(counts),
        'review_features': len(enriched['features']), 'enriched_features': matched,
        'geometry_changes': 0, 'verified_absolute_datum_rows': 0,
        'publication_state': 'private_review_only', 'output_file': output.name,
        'data_quality_complete': False,
        'notice': '공식 +100.3m 오프셋은 확인했으나 정확한 측량 기준면은 미확정입니다. 공식 지반고·레일면고를 보존하고, 지형 상대 위치를 변경하지 않았습니다.',
        'samples': [{'line': row['호선'], 'station': row['역명'], **item}
                    for row, item in zip(rows, evidence)
                    if any(flag != 'official_absolute_vertical_datum_unverified' for flag in item['vertical_quality_flags'])][:20]}
    atomic_json(root / '.local/audit/depth-vertical-quality.json', report)
    print(json.dumps(report, ensure_ascii=False))
    return report


if __name__ == '__main__':
    from .retired_3d import retired
    retired()
