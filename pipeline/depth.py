"""Join official station depth and position records with explicit matching rules."""
import csv
import io
import json
import math
import re
import requests
from pathlib import Path
from collections import defaultdict
from .core import LOCAL,PUBLIC,atomic_json,digest,now,publish_file
from .elevation import sample_heights

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
    raw=seoul_file('OA-13305','11','1','depth-20241104.csv')
    positions=seoul_file('OA-22534','1','2','coordinates-20250814.csv')
    coordinates=defaultdict(list)
    for row in csv_rows(positions):coordinates[join_key(row['호선'],row['역명'])].append(row)
    features=[];unmatched=[];invalid=[];joined=[]
    for row in csv_rows(raw):
        key=join_key(row['호선'],row['역명']);matches=coordinates[key]
        if len(matches)!=1:unmatched.append({'line':key[0],'name':key[1],'matches':len(matches)});continue
        try:
            d=float(row['정거장깊이']);rail=float(row['선로기준정거장깊이']);lon=float(matches[0]['경도']);lat=float(matches[0]['위도'])
            if not all(map(math.isfinite,[d,rail,lon,lat])) or not -50<d<150 or not 125<lon<129 or not 36<lat<39:raise ValueError()
            if abs((rail-1.1)-d)>.06:raise ValueError()
        except (ValueError,TypeError):invalid.append({'line':key[0],'name':key[1],'reason':'invalid number or inconsistent platform/rail depths'});continue
        joined.append((row,matches[0],lon,lat,d,rail))
    ground=sample_heights([r[2] for r in joined],[r[3] for r in joined]) if joined else []
    meta=json.loads(raw.with_suffix('.meta.json').read_text(encoding='utf-8'))
    for (row,position,lon,lat,d,rail),surface in zip(joined,ground):
        record_id=f'{row["호선"]}:{position["고유역번호(외부역코드)"]}'
        features.append({'type':'Feature','id':record_id,'geometry':{'type':'Point','coordinates':[lon,lat,float(surface-d)]},'properties':{
          'name':f'{row["역명"].strip()} · {row["호선"]}호선','platform_depth':d,'rail_depth':rail,'surface_height':float(surface),'display_height':float(surface-d),
          **station_vertical_evidence(row),
          'source_id':'seoul-depth','dataset_version':'depth-20241104 / coordinates-20250814','evidence_type':'official_record',
          'position_evidence':'calculation','description':f'승강장 기준 깊이 {d:g}m · 선로 기준 {rail:g}m. 기록 심도를 지형 높이에 적용한 개략 위치이며 실제 승강장 형상은 아닙니다. 음수는 지상·고가입니다.',
          'coordinate_source':'https://data.seoul.go.kr/dataList/OA-22534/F/1/datasetView.do','coordinate_hash':digest(positions),
          'provenance':{'source_id':'seoul-depth','source_record_id':record_id,'dataset_version':'20241104','observed_at':None,'retrieved_at':meta['retrieved_at'],'evidence_type':'official_record','input_hash':meta['sha256'],'transform_version':'depth-exact-line-name-v1','unit':'m below source ground'},
        }})
    hash_=__import__('hashlib').sha256(json.dumps(features,sort_keys=True).encode()).hexdigest()[:12]
    target=PUBLIC/'depth'/f'seoul-{hash_}.geojson';atomic_json(target,{'type':'FeatureCollection','features':features})
    audit={'source_rows':len(csv_rows(raw)),'position_rows':len(csv_rows(positions)),'joined':len(features),'unmatched':unmatched,'invalid':invalid,'aliases':ALIASES}
    atomic_json(LOCAL/'audit'/'depth-seoul.json',audit)
    if features:publish_file(target,asset_id='depth-seoul',layer='depth',format='geojson',bbox=[min(r[2] for r in joined),min(r[3] for r in joined),max(r[2] for r in joined),max(r[3] for r in joined)],source_id='seoul-depth',version='20241104',count=len(features))
    print(json.dumps(audit,ensure_ascii=False))


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
    review_vertical()
