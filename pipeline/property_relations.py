"""Release-scoped apartment identity, provenance, exact themes and bounded relations.

No networking, name matching, inferred coordinates, or new database service.
`verified` means the stated relation is supported by the specified source, not
that a source-provided coordinate or an entire dataset is independently complete.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3

KINDS = {'complex', 'building', 'address', 'region', 'transaction', 'station',
         'school', 'facility', 'development_plan', 'source'}
STATES = {'verified', 'unverified', 'inferred'}
PREDICATES = {'in_region', 'reported_address', 'reported_transaction', 'official_identity',
              'contains_building', 'nearby_straight_line', 'related_plan', 'documented_by'}
FACTS_SOURCE = 'https://data.seoul.go.kr/dataList/OA-15818/A/1/datasetView.do'
MAX_DOCUMENT_BYTES = 24 * 1024 * 1024
MAX_EDGES = 300


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def text(value, limit=512):
    if not isinstance(value, str) or not value.strip() or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('invalid_text')
    return value


def day(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        raise ValueError('invalid_date')
    return date.fromisoformat(value).isoformat()


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('invalid_number')
    return value


def coordinate(lon, lat):
    if not -180 <= number(lon) <= 180 or not -90 <= number(lat) <= 90:
        raise ValueError('invalid_coordinate')


def checked_document(path, expected_sha256):
    if not re.fullmatch('[a-f0-9]{64}', expected_sha256):
        raise ValueError('invalid_sha256')
    path = Path(path)
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError('document_too_large')
    body = path.read_bytes()
    if hashlib.sha256(body).hexdigest() != expected_sha256:
        raise ValueError('document_hash_mismatch')
    return json.loads(body)


class RelationStore:
    """One immutable input release per database; no silent cross-release joins."""

    def __init__(self, path, release_id, *, read_only=False):
        if not re.fullmatch(r'property-[a-f0-9]{16}', release_id):
            raise ValueError('invalid_release_id')
        self.release_id = release_id
        self.db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True) if read_only else sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        tables = {row[0] for row in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if tables:
            if 'meta' not in tables:
                self.close(); raise ValueError('not_a_relation_database')
            meta = dict(self.db.execute('SELECT key,value FROM meta'))
            if meta.get('release_id') != release_id:
                self.close(); raise ValueError('release_mismatch')
            if meta.get('schema_version') != '1':
                self.close(); raise ValueError('relation_schema_mismatch')
        if read_only:
            if not tables:
                self.close(); raise ValueError('empty_relation_database')
            self.db.execute('PRAGMA query_only=ON')
            return
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS evidence(id TEXT PRIMARY KEY,source_id TEXT NOT NULL,
            source_url TEXT NOT NULL,source_sha256 TEXT NOT NULL,record_id TEXT NOT NULL,
            method TEXT NOT NULL,observed_on TEXT NOT NULL,valid_from TEXT,valid_to TEXT,
            verification TEXT NOT NULL,claim_type TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS nodes(rowid INTEGER PRIMARY KEY,id TEXT UNIQUE NOT NULL,
            kind TEXT NOT NULL,label TEXT NOT NULL,attributes TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS node_evidence(node_id TEXT REFERENCES nodes(id),
            evidence_id TEXT REFERENCES evidence(id),PRIMARY KEY(node_id,evidence_id));
          CREATE TABLE IF NOT EXISTS edges(id TEXT PRIMARY KEY,subject TEXT REFERENCES nodes(id),
            predicate TEXT NOT NULL,object TEXT REFERENCES nodes(id),evidence_id TEXT REFERENCES evidence(id));
          CREATE INDEX IF NOT EXISTS edge_subject ON edges(subject,id);
          CREATE INDEX IF NOT EXISTS edge_object ON edges(object,id);
          CREATE TABLE IF NOT EXISTS facts(node_id TEXT REFERENCES nodes(id),field TEXT NOT NULL,
            value TEXT NOT NULL,numeric_value REAL,evidence_id TEXT REFERENCES evidence(id),
            PRIMARY KEY(node_id,field,evidence_id));
          CREATE INDEX IF NOT EXISTS fact_numeric ON facts(field,numeric_value,node_id);
          CREATE TABLE IF NOT EXISTS positions(node_id TEXT PRIMARY KEY REFERENCES nodes(id),
            longitude REAL NOT NULL,latitude REAL NOT NULL,crs TEXT NOT NULL,
            meaning TEXT NOT NULL,status TEXT NOT NULL,evidence_id TEXT REFERENCES evidence(id));
          CREATE VIRTUAL TABLE IF NOT EXISTS search_fts USING fts5(id UNINDEXED,label,address);
          CREATE VIRTUAL TABLE IF NOT EXISTS spatial_index USING rtree(id,min_lon,max_lon,min_lat,max_lat);
        ''')
        self.db.execute("INSERT OR IGNORE INTO meta VALUES('release_id',?)", (release_id,))
        self.db.execute("INSERT OR IGNORE INTO meta VALUES('schema_version','1')")
        self.db.commit()

    def close(self):
        self.db.close()

    @contextmanager
    def batch(self):
        self.db.execute('SAVEPOINT relation_import')
        try:
            yield
            self.db.execute('RELEASE relation_import')
        except Exception:
            self.db.execute('ROLLBACK TO relation_import')
            self.db.execute('RELEASE relation_import')
            raise

    def evidence(self, *, source_id, source_url, source_sha256, record_id, method,
                 observed_on, valid_from=None, valid_to=None, verification='verified', claim_type='source'):
        for value in (source_id, record_id, method):
            text(value)
        text(source_url, 2048)
        if not re.fullmatch(r'https://[^\s]+', source_url) or not re.fullmatch('[a-f0-9]{64}', source_sha256):
            raise ValueError('invalid_provenance')
        day(observed_on)
        if valid_from is not None: day(valid_from)
        if valid_to is not None: day(valid_to)
        if valid_from and valid_to and valid_from > valid_to:
            raise ValueError('invalid_validity_range')
        if verification not in STATES:
            raise ValueError('invalid_verification')
        if claim_type not in {'source', 'calculated', 'inferred'} or (claim_type == 'inferred') != (verification == 'inferred'):
            raise ValueError('invalid_claim_type')
        data = dict(source_id=source_id, source_url=source_url, source_sha256=source_sha256,
                    record_id=record_id, method=method, observed_on=observed_on,
                    valid_from=valid_from, valid_to=valid_to, verification=verification, claim_type=claim_type)
        identity = 'evidence:' + digest(data)
        self.db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?,?,?,?,?,?,?,?,?)', (identity, *data.values()))
        return identity

    def node(self, identity, kind, label, evidence_id, attributes=None):
        text(identity); text(label)
        if ':' not in identity or kind not in KINDS:
            raise ValueError('invalid_node_identity')
        if not self.db.execute('SELECT 1 FROM evidence WHERE id=?', (evidence_id,)).fetchone():
            raise ValueError('missing_node_evidence')
        attrs = attributes or {}
        if not isinstance(attrs, dict) or len(canonical(attrs)) > 8192:
            raise ValueError('invalid_attributes')
        encoded = canonical(attrs)
        found = self.db.execute('SELECT kind,label,attributes FROM nodes WHERE id=?', (identity,)).fetchone()
        if found and tuple(found) != (kind, label, encoded):
            raise ValueError('conflicting_node_identity')
        cursor = self.db.execute('INSERT OR IGNORE INTO nodes(id,kind,label,attributes) VALUES(?,?,?,?)',
                                (identity, kind, label, encoded))
        self.db.execute('INSERT OR IGNORE INTO node_evidence VALUES(?,?)', (identity, evidence_id))
        if cursor.rowcount:
            self.db.execute('INSERT INTO search_fts VALUES(?,?,?)', (identity, label, str(attrs.get('address', ''))))
        return identity

    def edge(self, subject, predicate, object_id, evidence_id):
        if predicate not in PREDICATES or subject == object_id:
            raise ValueError('invalid_relation')
        identity = 'relation:' + digest([subject, predicate, object_id, evidence_id])
        self.db.execute('INSERT OR IGNORE INTO edges VALUES(?,?,?,?,?)',
                        (identity, subject, predicate, object_id, evidence_id))
        return identity

    def fact(self, node_id, field, value, evidence_id):
        if not re.fullmatch('[a-z][a-z0-9_]{0,63}', field):
            raise ValueError('invalid_fact_field')
        if value is None:
            return  # Missing is not zero, and is not indexed as an available filter.
        numeric = number(value) if isinstance(value, (int, float)) else None
        encoded = canonical(value)
        if len(encoded) > 2048:
            raise ValueError('fact_too_large')
        found = self.db.execute('SELECT value FROM facts WHERE node_id=? AND field=? AND evidence_id=?',
                                (node_id, field, evidence_id)).fetchone()
        if found and found[0] != encoded:
            raise ValueError('conflicting_fact')
        self.db.execute('INSERT OR IGNORE INTO facts VALUES(?,?,?,?,?)', (node_id, field, encoded, numeric, evidence_id))

    def position(self, node_id, longitude, latitude, evidence_id, *, status, meaning, crs='EPSG:4326'):
        coordinate(longitude, latitude)
        if crs != 'EPSG:4326' or status not in {'verified', 'source_record', 'unverified'} or meaning not in {
                'representative_point', 'entrance', 'original_node', 'area_representative_point', 'line_midpoint'}:
            raise ValueError('invalid_position_contract')
        values = (longitude, latitude, crs, meaning, status, evidence_id)
        old = self.db.execute('SELECT longitude,latitude,crs,meaning,status,evidence_id FROM positions WHERE node_id=?',
                              (node_id,)).fetchone()
        if old and tuple(old) != values:
            raise ValueError('conflicting_position')
        self.db.execute('INSERT OR IGNORE INTO positions VALUES(?,?,?,?,?,?,?)', (node_id, *values))
        row = self.db.execute('SELECT rowid FROM nodes WHERE id=?', (node_id,)).fetchone()
        if status != 'unverified':
            self.db.execute('INSERT OR IGNORE INTO spatial_index VALUES(?,?,?,?,?)', (row[0], longitude, longitude, latitude, latitude))

    @staticmethod
    def _validity(as_of, alias='ev'):
        day(as_of)
        return f"{alias}.observed_on<=? AND ({alias}.valid_from IS NULL OR {alias}.valid_from<=?) AND ({alias}.valid_to IS NULL OR {alias}.valid_to>=?)"

    def graph(self, root_id, *, as_of, hops=2, limit=100, include_inferred=False):
        text(root_id)
        if type(hops) is not int or hops not in (0, 1, 2) or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('relation_query_limit')
        validity = self._validity(as_of)
        states = ('verified', 'inferred') if include_inferred else ('verified',)
        status_sql = ','.join('?' for _ in states)
        root = self.db.execute(f'''SELECT n.* FROM nodes n WHERE n.id=? AND EXISTS
            (SELECT 1 FROM node_evidence ne JOIN evidence ev ON ev.id=ne.evidence_id
             WHERE ne.node_id=n.id AND {validity} AND ev.verification IN ({status_sql}))''',
            (root_id, as_of, as_of, as_of, *states)).fetchone()
        if root is None:
            return dict(schema_version=1, release_id=self.release_id, root_id=root_id, as_of=as_of,
                        nodes=[], edges=[], evidence=[], truncated=False)
        selected = {root_id: root}; edges = {}; frontier = {root_id}; truncated = False
        for _ in range(hops):
            following = set()
            for identity in sorted(frontier):
                # Bounded SQL result even when a source region has millions of edges.
                # Scan a fixed candidate budget per direction through covering
                # identity indexes; expired relations do not create unbounded scans.
                candidates = {}
                for direction in ('subject','object'):
                    found = self.db.execute(f'SELECT * FROM edges WHERE {direction}=? ORDER BY id LIMIT ?',
                                            (identity, MAX_EDGES+1)).fetchall()
                    if len(found)>MAX_EDGES: truncated=True
                    for row in found[:MAX_EDGES]: candidates[row['id']]=row
                rows = []
                for _, edge in sorted(candidates.items()):
                    accepted=self.db.execute(f'''SELECT 1 FROM evidence ev WHERE ev.id=?
                        AND ev.verification IN ({status_sql}) AND {validity}''',
                        (edge['evidence_id'],*states,as_of,as_of,as_of)).fetchone()
                    if accepted: rows.append(edge)
                if len(rows) > MAX_EDGES: truncated = True
                for edge in rows[:MAX_EDGES]:
                    other = edge['object'] if edge['subject'] == identity else edge['subject']
                    if other not in selected:
                        if len(selected) >= limit:
                            truncated = True
                            continue
                        selected[other] = self.db.execute('SELECT * FROM nodes WHERE id=?', (other,)).fetchone()
                        following.add(other)
                    if len(edges) < MAX_EDGES:
                        edges[edge['id']] = dict(edge)
                    else:
                        truncated = True
            frontier = following
        evidence_ids = {e['evidence_id'] for e in edges.values()}
        node_evidence = {}
        for identity in selected:
            rows = self.db.execute(f'''SELECT ne.evidence_id FROM node_evidence ne
                JOIN evidence ev ON ev.id=ne.evidence_id WHERE ne.node_id=? AND {validity}
                AND ev.verification IN ({status_sql}) ORDER BY ne.evidence_id LIMIT 9''',
                (identity, as_of, as_of, as_of, *states)).fetchall()
            if len(rows)>8: truncated=True
            node_evidence[identity] = []
            for row in rows[:8]:
                if row[0] in evidence_ids or len(evidence_ids)<512:
                    evidence_ids.add(row[0]); node_evidence[identity].append(row[0])
                else: truncated=True
        nodes = [dict(id=r['id'], kind=r['kind'], label=r['label'], attributes=json.loads(r['attributes']),
                      evidence_ids=node_evidence[r['id']])
                 for _, r in sorted(selected.items())]
        evidence = [dict(self.db.execute('SELECT * FROM evidence WHERE id=?', (key,)).fetchone()) for key in sorted(evidence_ids)]
        return dict(schema_version=1, release_id=self.release_id, root_id=root_id, as_of=as_of,
                    nodes=nodes, edges=sorted(edges.values(), key=lambda e: e['id']), evidence=evidence, truncated=truncated)

    def search(self, query, *, limit=30):
        text(query, 200)
        if type(limit) is not int or not 1 <= limit <= 100: raise ValueError('search_limit')
        tokens = re.findall(r'[^\W_]+', query, re.UNICODE)[:10]
        if not tokens: return []
        expression = ' AND '.join('"' + token + '"*' for token in tokens)
        return [dict(row) for row in self.db.execute('''SELECT n.id,n.kind,n.label FROM search_fts s
            JOIN nodes n ON n.id=s.id WHERE search_fts MATCH ? ORDER BY rank,n.id LIMIT ?''', (expression, limit))]

    def nearby(self, longitude, latitude, *, as_of, radius_m=3000, limit=100):
        coordinate(longitude, latitude); day(as_of)
        if not 0 < number(radius_m) <= 3000 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('spatial_query_limit')
        lat_delta = radius_m / 110000
        lon_delta = min(180, lat_delta / max(.001, math.cos(math.radians(latitude))))
        rows = self.db.execute(f'''SELECT n.id,n.kind,n.label,p.* FROM spatial_index r JOIN nodes n ON n.rowid=r.id
            JOIN positions p ON p.node_id=n.id JOIN evidence ev ON ev.id=p.evidence_id
            WHERE r.min_lon<=? AND r.max_lon>=? AND r.min_lat<=? AND r.max_lat>=? AND ev.verification='verified' AND {self._validity(as_of)}
            ORDER BY n.id LIMIT 1001''', (longitude+lon_delta, longitude-lon_delta, latitude+lat_delta,
                                         latitude-lat_delta, as_of, as_of, as_of)).fetchall()
        result = []
        for row in rows[:1000]:
            dlat = math.radians(row['latitude'] - latitude); dlon = math.radians(row['longitude'] - longitude)
            a = math.sin(dlat/2)**2 + math.cos(math.radians(latitude))*math.cos(math.radians(row['latitude']))*math.sin(dlon/2)**2
            distance = 6371008.8 * 2 * math.asin(min(1, math.sqrt(a)))
            if distance <= radius_m:
                result.append({**dict(row), 'distance_m': round(distance, 1), 'distance_kind': 'straight_line'})
        result.sort(key=lambda r: (r['distance_m'], r['id']))
        return {'release_id': self.release_id, 'records': result[:limit], 'truncated': len(rows)>1000 or len(result)>limit}

    def theme(self, conditions, *, as_of, limit=100):
        """Exact, source-backed field filters. No price predictions or missing-as-zero."""
        if not isinstance(conditions, dict) or not conditions or len(conditions)>8 or type(limit) is not int or not 1<=limit<=100:
            raise ValueError('theme_query_limit')
        allowed = {'households', 'parking', 'buildings', 'build_year', 'approved_year', 'parking_per_household'}
        clauses = []; params = []; unavailable = []
        for field, bounds in sorted(conditions.items()):
            if field not in allowed or not isinstance(bounds, dict) or not set(bounds)<= {'min','max'} or not bounds:
                raise ValueError('unsupported_theme_condition')
            low = bounds.get('min'); high = bounds.get('max')
            for value in (low, high):
                if value is not None: number(value)
            if low is not None and high is not None and low>high: raise ValueError('invalid_theme_range')
            valid = self._validity(as_of)
            available = self.db.execute(f'''SELECT 1 FROM facts f JOIN evidence ev ON ev.id=f.evidence_id
                WHERE f.field=? AND f.numeric_value IS NOT NULL AND ev.verification='verified' AND {valid} LIMIT 1''',
                (field, as_of, as_of, as_of)).fetchone()
            if not available: unavailable.append(field)
            clause = f"EXISTS (SELECT 1 FROM facts f JOIN evidence ev ON ev.id=f.evidence_id WHERE f.node_id=n.id AND f.field=? AND ev.verification='verified' AND {valid}"
            values = [field, as_of, as_of, as_of]
            if low is not None: clause += ' AND f.numeric_value>=?'; values.append(low)
            if high is not None: clause += ' AND f.numeric_value<=?'; values.append(high)
            clauses.append(clause+')'); params.extend(values)
            # Conflicting current assertions cannot silently pass by choosing
            # whichever provider happens to fit the user's filter.
            clauses.append(f'''(SELECT COUNT(DISTINCT f.numeric_value) FROM facts f JOIN evidence ev ON ev.id=f.evidence_id
                WHERE f.node_id=n.id AND f.field=? AND ev.verification='verified' AND {valid})=1''')
            params.extend((field,as_of,as_of,as_of))
        if unavailable:
            return dict(release_id=self.release_id, status='unavailable', conditions=conditions, unavailable_fields=unavailable, records=[], truncated=False)
        matches = self.db.execute("SELECT n.id,n.label FROM nodes n WHERE n.kind='complex' AND " + ' AND '.join(clauses) + ' ORDER BY n.id LIMIT ?', (*params, limit+1)).fetchall()
        records = []
        for row in matches[:limit]:
            supports = []
            for field in conditions:
                supports.extend(dict(f) for f in self.db.execute(f'''SELECT f.field,f.numeric_value,ev.* FROM facts f
                    JOIN evidence ev ON ev.id=f.evidence_id WHERE f.node_id=? AND f.field=?
                    AND ev.verification='verified' AND {self._validity(as_of)}''', (row['id'], field, as_of, as_of, as_of)))
            records.append({**dict(row), 'evidence': supports})
        return dict(release_id=self.release_id, status='ready', conditions=conditions, unavailable_fields=[], records=records, truncated=len(matches)>limit)


def ingest_official_facts(store, path, expected_sha256):
    """Use the existing audited source-ID/address linkage; never accept XY here."""
    document = checked_document(path, expected_sha256)
    if document.get('schema_version') != 1 or document.get('property_release_id') != store.release_id or document.get('source') != FACTS_SOURCE or document.get('identity_method') != 'existing_unique_official_road_address_and_name' or document.get('coordinate_verification') != 'not_performed':
        raise ValueError('facts_contract_mismatch')
    rows = document['rows']; seen = set(); kapt_seen = set()
    if not isinstance(rows, list) or len(rows)>3000: raise ValueError('facts_row_limit')
    observed = day(document['retrieved_at'][:10])
    with store.batch():
        for row in rows:
            identity = row['complex_id']; kapt = row['kapt_code']
            if not re.fullmatch(r'molit-apt:11\d{3}:[A-Za-z0-9_-]{1,64}', identity) or not re.fullmatch(r'A\d{8,12}', kapt) or identity in seen or kapt in kapt_seen:
                raise ValueError('duplicate_or_invalid_identity')
            seen.add(identity); kapt_seen.add(kapt)
            if row['classification'] not in {'아파트','주상복합'}: raise ValueError('unsupported_property_type')
            evidence = store.evidence(source_id='seoul-apartment-facts', source_url=FACTS_SOURCE,
                source_sha256=expected_sha256, record_id=kapt, method=document['identity_method'], observed_on=observed)
            # This file has no apartment name; do not guess one from its address.
            label = row.get('road_address') or identity
            store.node(identity, 'complex', label, evidence, {'address': row.get('road_address'), 'lawd_code': identity.split(':')[1]})
            official = store.node('kapt:'+kapt, 'source', kapt, evidence, {'namespace':'kapt'})
            store.edge(identity, 'official_identity', official, evidence)
            region = 'legal-region:'+identity.split(':')[1]
            store.node(region, 'region', identity.split(':')[1], evidence, {'code_system':'lawd', 'code':identity.split(':')[1]})
            store.edge(identity, 'in_region', region, evidence)
            if row.get('road_address'):
                address = store.node('reported-address:'+digest([identity,row['road_address']]), 'address', row['road_address'], evidence)
                store.edge(identity, 'reported_address', address, evidence)
            for field in ('households','parking','buildings'):
                value = row.get(field)
                if value is not None and (type(value) is not int or not 0<=value<=1_000_000): raise ValueError('invalid_official_count')
                store.fact(identity, field, value, evidence)
            if row.get('approved_on'):
                store.fact(identity, 'approved_on', day(row['approved_on']), evidence)
                store.fact(identity, 'approved_year', int(row['approved_on'][:4]), evidence)
            if row.get('households') and row.get('parking') is not None:
                calculated = store.evidence(source_id='seoul-apartment-facts', source_url=FACTS_SOURCE,
                    source_sha256=expected_sha256, record_id=kapt, method='parking_divided_by_households',
                    observed_on=observed, claim_type='calculated')
                store.fact(identity, 'parking_per_household', row['parking']/row['households'], calculated)
    return {'source_rows':len(rows), 'complexes':len(seen), 'positions_imported':0}


def ingest_osm_chunk(store, path, expected_sha256, *, source):
    document = checked_document(path, expected_sha256)
    if document.get('schema') != 1 or not isinstance(document.get('records'), list) or len(document['records'])>20000:
        raise ValueError('poi_contract_mismatch')
    if source.get('id')!='osm' or source.get('license')!='ODbL-1.0' or not re.fullmatch('[a-f0-9]{64}', source.get('sha256','')):
        raise ValueError('poi_source_mismatch')
    observed = day(source['asOf'][:10]); seen = set()
    with store.batch():
        for row in document['records']:
            source_id = row['id']
            if not re.fullmatch(r'(node|way|relation)/[1-9][0-9]*', source_id) or source_id in seen:
                raise ValueError('duplicate_or_invalid_poi_identity')
            seen.add(source_id)
            category = row['category']; subtype = row['type']
            allowed = {'transport':{'subway','rail','bus'}, 'school':{'elementary','middle','high','school','university'},
                       'life':{'medical','shopping','park','public'}}
            if category not in allowed or subtype not in allowed[category]: raise ValueError('poi_category_mismatch')
            evidence = store.evidence(source_id='osm', source_url='https://www.openstreetmap.org/'+source_id,
                source_sha256=expected_sha256, record_id=source_id, method='osm_record', observed_on=observed)
            kind = 'station' if category=='transport' else 'school' if category=='school' else 'facility'
            identity = store.node('osm:'+source_id, kind, row['name'], evidence,
                {'category':category,'type':subtype,'address':row.get('address'),'source_snapshot_sha256':source['sha256'],'license':'ODbL-1.0'})
            store.position(identity,row['longitude'],row['latitude'],evidence,status='source_record',meaning=row['positionMethod'])
    return {'source_rows':len(seen), 'coverage_complete':False, 'school_assignment':False}
