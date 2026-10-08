import hashlib
import json
import sqlite3

import pytest

from pipeline.property_relations import (
    FACTS_SOURCE, RelationStore, ingest_official_facts, ingest_osm_chunk,
)

RELEASE = 'property-ceeff63959643461'
AS_OF = '2026-10-08'


@pytest.fixture
def store(tmp_path):
    value = RelationStore(tmp_path/'relations.sqlite', RELEASE)
    yield value
    value.close()


def evidence(store, record='1', **overrides):
    args = dict(source_id='official-fixture', source_url='https://example.org/dataset',
                source_sha256='a'*64, record_id=record, method='official_id', observed_on='2026-09-01')
    return store.evidence(**(args | overrides))


def node(store, identity='complex:a', **ev):
    return store.node(identity, 'complex', '같은 이름', evidence(store, identity, **ev))


def document(tmp_path, value, name='facts.json'):
    path = tmp_path/name
    body = json.dumps(value, ensure_ascii=False).encode()
    path.write_bytes(body)
    return path, hashlib.sha256(body).hexdigest()


def facts():
    return dict(schema_version=1, property_release_id=RELEASE, source=FACTS_SOURCE,
        identity_method='existing_unique_official_road_address_and_name', coordinate_verification='not_performed',
        retrieved_at='2026-09-23T04:36:36Z', rows=[dict(complex_id='molit-apt:11110:11110-102', kapt_code='A11034001',
        classification='아파트', households=200, parking=250, buildings=2,
        road_address='서울특별시 종로구 돈화문로 1', approved_on='2003-11-29')])


def test_idempotence_and_same_name_does_not_merge(store):
    a=node(store); b=node(store,'complex:b'); node(store)
    assert a!=b
    assert store.db.execute('SELECT count(*) FROM nodes').fetchone()[0]==2
    assert len(store.search('같은'))==2
    with pytest.raises(ValueError,match='conflicting_node_identity'):
        store.node(a,'complex','다른 이름',evidence(store))


def test_release_pinning(tmp_path):
    path=tmp_path/'db.sqlite'; first=RelationStore(path,RELEASE); first.close()
    with pytest.raises(ValueError,match='release_mismatch'):
        RelationStore(path,'property-0000000000000000')


def test_read_only_api_access_and_unrelated_database_protection(tmp_path):
    path=tmp_path/'relations.sqlite'; writer=RelationStore(path,RELEASE)
    root=node(writer); writer.db.commit(); writer.close()
    reader=RelationStore(path,RELEASE,read_only=True)
    try:
        assert reader.graph(root,as_of=AS_OF)['nodes']
        with pytest.raises(sqlite3.OperationalError): node(reader,'complex:write')
    finally: reader.close()
    other=tmp_path/'other.sqlite'; connection=sqlite3.connect(other)
    connection.execute('CREATE TABLE user_data(value TEXT)');connection.commit();connection.close()
    with pytest.raises(ValueError,match='not_a_relation_database'): RelationStore(other,RELEASE)
    connection=sqlite3.connect(other)
    assert [r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")]==['user_data']
    connection.close()


def test_missing_provenance_foreign_key_and_invalid_claim(store):
    with pytest.raises(ValueError,match='missing_node_evidence'): store.node('complex:x','complex','x','unknown')
    assert store.db.execute('SELECT count(*) FROM nodes').fetchone()[0]==0
    with pytest.raises(ValueError,match='invalid_provenance'): evidence(store,source_sha256='wrong')
    with pytest.raises(ValueError,match='invalid_claim_type'): evidence(store,verification='inferred')
    with pytest.raises(ValueError,match='invalid_validity_range'):
        evidence(store,valid_from='2026-10-02',valid_to='2026-10-01')


def test_graph_temporal_validity_and_future_observation(store):
    root=node(store); old=node(store,'complex:old'); future=node(store,'complex:future')
    store.edge(root,'related_plan',old,evidence(store,'old',valid_to='2026-09-30'))
    store.edge(root,'related_plan',future,evidence(store,'future',observed_on='2026-11-01'))
    assert len(store.graph(root,as_of=AS_OF)['nodes'])==1
    assert len(store.graph(root,as_of='2026-09-30')['nodes'])==2
    assert len(store.graph(root,as_of='2026-11-01')['nodes'])==2


def test_graph_unverified_and_inferred_are_explicit(store):
    root=node(store); second=node(store,'complex:b'); third=node(store,'complex:c')
    store.edge(root,'related_plan',second,evidence(store,'b',verification='unverified'))
    store.edge(root,'nearby_straight_line',third,evidence(store,'c',verification='inferred',claim_type='inferred'))
    assert store.graph(root,as_of=AS_OF)['edges']==[]
    result=store.graph(root,as_of=AS_OF,include_inferred=True)
    assert len(result['edges'])==1
    assert any(e['verification']=='inferred' for e in result['evidence'])
    unverified=node(store,'complex:unverified',verification='unverified')
    assert store.graph(unverified,as_of=AS_OF)['nodes']==[]


def test_graph_two_hops_max100_nodes_and300_edges(store):
    root=node(store); previous=root
    for i in range(3):
        current=node(store,f'complex:chain{i}')
        store.edge(previous,'related_plan',current,evidence(store,f'chain{i}')); previous=current
    result=store.graph(root,as_of=AS_OF)
    assert {n['id'] for n in result['nodes']}=={root,'complex:chain0','complex:chain1'}
    for i in range(140):
        target=node(store,f'complex:fan{i}')
        store.edge(root,'related_plan',target,evidence(store,f'fan{i}'))
    result=store.graph(root,as_of=AS_OF)
    assert len(result['nodes'])==100 and len(result['edges'])<=300 and result['truncated']
    ids={n['id'] for n in result['nodes']}
    assert all(e['subject'] in ids and e['object'] in ids for e in result['edges'])
    with pytest.raises(ValueError,match='relation_query_limit'): store.graph(root,as_of=AS_OF,hops=3)
    with pytest.raises(ValueError,match='relation_query_limit'): store.graph(root,as_of=AS_OF,limit=101)


@pytest.mark.parametrize('longitude,latitude',[(181,37),(127,91),(float('nan'),37),(True,37)])
def test_invalid_coordinates(store,longitude,latitude):
    identity=node(store)
    with pytest.raises(ValueError):
        store.position(identity,longitude,latitude,evidence(store),status='verified',meaning='representative_point')


def test_spatial_verified_source_and_unverified_coordinates(store):
    for i,status in enumerate(('verified','source_record','unverified')):
        identity=node(store,f'complex:{i}')
        store.position(identity,127+i*.001,37.5,evidence(store,str(i)),status=status,meaning='original_node')
    result=store.nearby(127,37.5,as_of=AS_OF)
    assert [r['id'] for r in result['records']]==['complex:0','complex:1']
    assert all(r['distance_kind']=='straight_line' for r in result['records'])
    assert result['records'][1]['status']=='source_record'
    with pytest.raises(ValueError,match='spatial_query_limit'): store.nearby(127,37.5,as_of=AS_OF,radius_m=4000)


def test_fts_quotes_sql_syntax_as_search_tokens(store):
    node(store)
    assert store.search('같은')
    assert store.search('" OR *')==[]
    assert store.db.execute('SELECT count(*) FROM nodes').fetchone()[0]==1


def test_theme_missing_zero_unverified_conflicts_and_source_trace(store):
    root=node(store); verified=evidence(store)
    store.fact(root,'parking',0,verified)
    store.fact(root,'households',200,verified)
    store.fact(root,'build_year',None,verified)
    assert store.theme({'build_year':{'min':2000}},as_of=AS_OF)['status']=='unavailable'
    result=store.theme({'parking':{'max':0},'households':{'min':100}},as_of=AS_OF)
    assert result['records'][0]['id']==root
    assert result['records'][0]['evidence'][0]['source_sha256']=='a'*64
    store.fact(root,'households',300,evidence(store,'conflict'))
    assert store.theme({'households':{'min':100}},as_of=AS_OF)['records']==[]
    store.fact(root,'build_year',2001,evidence(store,'unverified',verification='unverified'))
    assert store.theme({'build_year':{'min':2000}},as_of=AS_OF)['status']=='unavailable'


def test_official_facts_adapter_idempotence_and_no_coordinate_promotion(store,tmp_path):
    value=facts(); value['rows'][0]['longitude']=127.2; value['rows'][0]['latitude']=37.4
    path,sha=document(tmp_path,value)
    result=ingest_official_facts(store,path,sha)
    assert result=={'source_rows':1,'complexes':1,'positions_imported':0}
    before=store.db.total_changes
    ingest_official_facts(store,path,sha)
    assert store.db.total_changes==before
    assert store.db.execute('SELECT count(*) FROM positions').fetchone()[0]==0
    result=store.theme({'parking_per_household':{'min':1.2}},as_of=AS_OF)
    assert len(result['records'])==1
    assert result['records'][0]['evidence'][0]['claim_type']=='calculated'
    assert store.theme({'approved_year':{'min':2003,'max':2003}},as_of=AS_OF)['records']


def test_official_facts_atomic_duplicate_failure_and_hash(store,tmp_path):
    value=facts(); value['rows'].append(value['rows'][0].copy()); path,sha=document(tmp_path,value)
    with pytest.raises(ValueError,match='duplicate_or_invalid_identity'): ingest_official_facts(store,path,sha)
    assert store.db.execute('SELECT count(*) FROM nodes').fetchone()[0]==0
    with pytest.raises(ValueError,match='document_hash_mismatch'): ingest_official_facts(store,path,'0'*64)


def test_osm_adapter_original_id_and_source_record_meaning(store,tmp_path):
    row=dict(id='node/42',name='같은 학교',category='school',type='elementary',longitude=127.1,latitude=37.5,
             positionMethod='original_node')
    path,sha=document(tmp_path,{'schema':1,'records':[row]},'poi.json')
    source={'id':'osm','license':'ODbL-1.0','sha256':'b'*64,'asOf':'2026-09-15T20:20:37Z'}
    assert ingest_osm_chunk(store,path,sha,source=source)['school_assignment'] is False
    before=store.db.total_changes; ingest_osm_chunk(store,path,sha,source=source)
    assert store.db.total_changes==before
    result=store.nearby(127.1,37.5,as_of=AS_OF)
    assert result['records'][0]['id']=='osm:node/42'
    assert result['records'][0]['status']=='source_record'
    graph=store.graph('osm:node/42',as_of=AS_OF)
    assert graph['evidence'][0]['source_url']=='https://www.openstreetmap.org/node/42'
    assert graph['edges']==[]  # No school assignment or made-up apartment relation.


def test_real_published_facts_can_be_ingested_with_provenance(tmp_path):
    from pathlib import Path
    path=Path(__file__).resolve().parents[1]/'src/data/seoul-apartment-facts-ceeff63959643461.json'
    source=json.loads(path.read_bytes()); store=RelationStore(tmp_path/'real.sqlite',RELEASE)
    try:
        result=ingest_official_facts(store,path,hashlib.sha256(path.read_bytes()).hexdigest())
        assert result['complexes']==len(source['rows'])==827
        assert store.db.execute('SELECT count(*) FROM positions').fetchone()[0]==0
        assert store.theme({'households':{'min':1000}},as_of=AS_OF)['records']
        assert store.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    finally: store.close()
