import hashlib
import json

import pytest
from shapely.geometry import Point, Polygon

from pipeline import property_poi as poi


@pytest.mark.parametrize('tags,expected', [
    ({'amenity': 'school', 'name': '서울초등학교'}, 'school'),
    ({'amenity': 'school', 'isced:level': '1'}, 'elementary'),
    ({'amenity': 'school', 'isced:level': '2'}, 'middle'),
    ({'amenity': 'school', 'isced:level': '3'}, 'high'),
    ({'amenity': 'school', 'isced:level': '1;2'}, 'school'),
    ({'amenity': 'university'}, 'university'),
    ({'railway': 'station', 'station': 'subway'}, 'subway'),
    ({'railway': 'halt'}, 'rail'),
    ({'highway': 'bus_stop'}, 'bus'),
    ({'public_transport': 'platform', 'bus': 'yes'}, 'bus'),
    ({'public_transport': 'platform'}, None),
    ({'amenity': 'hospital'}, 'medical'),
    ({'healthcare': 'doctor'}, 'medical'),
    ({'shop': 'supermarket'}, 'shopping'),
    ({'leisure': 'park'}, 'park'),
    ({'amenity': 'library'}, 'public'),
    ({'amenity': 'school', 'disused': 'yes'}, None),
    ({'railway': 'station', 'construction': 'station'}, None),
    ({'amenity': 'school', 'proposed': 'yes'}, None),
    ({'amenity': 'school', 'disused': 'no'}, 'school'),
    ({'building': 'school', 'name': '학교'}, None),
])
def test_explicit_classification_and_lifecycle(tags, expected):
    assert poi.classify(tags) == expected


def test_scope_keeps_holes_and_boundary():
    region = Polygon([(126, 36), (128, 36), (128, 38), (126, 38)],
                     [[(126.9, 36.9), (127.1, 36.9), (127.1, 37.1), (126.9, 37.1)]])
    scope = poi.Scope([region], ['11'])
    assert scope.region(Point(126, 36)) == '11'
    assert scope.region(Point(127, 37)) is None
    assert scope.region(Point(129, 37)) is None


def record(index, lon=127.01, lat=37.51):
    return {'id': f'node/{index}', 'name': f'시설 {index}', 'category': 'life',
            'type': 'public', 'longitude': lon, 'latitude': lat,
            'positionMethod': 'original_node', 'scopeRegionCode': '11',
            'sourceTags': {'amenity': 'library'}}


def test_spatial_chunks_preserve_all_ids_and_coincident_records():
    rows = [record(i, 127.001 + (i % 2) * .035, 37.501 + (i % 3) * .015) for i in range(40)]
    rows += [record(i) for i in range(40, 70)]
    chunks = list(poi.partition(rows, 1024))
    restored = []
    for key, (west, south, east, north), body, count in chunks:
        assert len(body) <= 1024
        decoded = json.loads(body)['records']
        assert len(decoded) == count
        for row in decoded:
            assert west <= row['longitude'] <= east
            assert south <= row['latitude'] <= north
        restored.extend(row['id'] for row in decoded)
    assert sorted(restored) == sorted(row['id'] for row in rows)
    assert chunks == list(poi.partition(list(reversed(rows)), 1024))


def test_publish_hashes_manifest_last_and_no_overwrite(tmp_path):
    rows = [record(i) for i in range(8)]
    source = {'id': 'fixture'}
    scope = {'regionCodes': ['11']}
    audit = poi.publish(rows, source, scope, {'included': 8}, tmp_path, 1024)
    manifest = json.loads((tmp_path / 'manifest.json').read_bytes())
    assert audit['uniqueSourceIds'] == 8
    assert audit['manifestSha256'] == poi.sha256(tmp_path / 'manifest.json')
    for chunk in manifest['chunks']:
        body = (tmp_path / chunk['file']).read_bytes()
        digest = hashlib.sha256(body).hexdigest()
        assert chunk['file'] == f'poi-{digest}.json'
        assert chunk['sha256'] == digest
        assert chunk['bytes'] == len(body)
    poi.publish(rows, source, scope, {'included': 8}, tmp_path, 1024)
    with pytest.raises(ValueError, match='Refusing to replace'):
        poi.write_immutable(tmp_path / 'manifest.json', b'different')


def test_budget_fails_before_publishing_and_duplicates_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(poi, 'MAX_CHUNKS', 1)
    with pytest.raises(ValueError, match='budget'):
        poi.publish([record(1), record(2, lon=128)], {}, {}, {}, tmp_path)
    assert not list(tmp_path.iterdir())
    with pytest.raises(ValueError, match='Duplicate POI ID'):
        poi.publish([record(1), record(1)], {}, {}, {}, tmp_path)


def test_source_metadata_mismatch_prevents_reading(tmp_path):
    pbf = tmp_path / 'source.osm.pbf'
    pbf.write_bytes(b'fixture')
    pbf.with_suffix('.pbf.meta.json').write_text(json.dumps({'url': poi.SOURCE_URL,
        'sha256': '0' * 64, 'bytes': 7}))
    with pytest.raises(ValueError, match='metadata'):
        poi.verify_source(pbf)


def test_actual_osmium_nodes_lines_areas_and_relation_ids(tmp_path):
    # Exercises the real area assembler and native tag filter, including an
    # unnamed stop, a relation hole, outside-scope points, and independent IDs.
    xml = '''<osm version="0.6">
      <node id="1" lat="37.51" lon="127.01"><tag k="highway" v="bus_stop"/></node>
      <node id="2" lat="37.52" lon="127.02"><tag k="amenity" v="school"/><tag k="name" v="한국초등학교"/></node>
      <node id="3" lat="37.53" lon="127.03"><tag k="amenity" v="school"/><tag k="isced:level" v="1"/></node>
      <node id="4" lat="38.52" lon="128.02"><tag k="amenity" v="hospital"/></node>
      <node id="5" lat="37.50" lon="127.00"/><node id="6" lat="37.50" lon="127.05"/>
      <node id="7" lat="37.55" lon="127.05"/><node id="8" lat="37.55" lon="127.00"/>
      <node id="9" lat="37.51" lon="127.01"/><node id="10" lat="37.51" lon="127.04"/>
      <node id="11" lat="37.54" lon="127.04"/><node id="12" lat="37.54" lon="127.01"/>
      <way id="20"><nd ref="5"/><nd ref="6"/><tag k="amenity" v="bus_station"/><tag k="area" v="no"/></way>
      <way id="21"><nd ref="5"/><nd ref="6"/><nd ref="7"/><nd ref="8"/><nd ref="5"/><tag k="amenity" v="school"/></way>
      <way id="22"><nd ref="9"/><nd ref="10"/><nd ref="11"/><nd ref="12"/><nd ref="9"/></way>
      <relation id="30"><member type="way" ref="21" role="outer"/><member type="way" ref="22" role="inner"/>
      <tag k="type" v="multipolygon"/><tag k="leisure" v="park"/></relation>
    </osm>'''
    path = tmp_path / 'fixture.osm'
    path.write_text(xml, encoding='utf-8')
    scope = poi.Scope([Polygon([(126, 36), (128, 36), (128, 38), (126, 38)])], ['11'])
    rows, counters = poi.extract(path, scope)
    by_id = {row['id']: row for row in rows}
    assert set(by_id) == {'node/1', 'node/2', 'node/3', 'way/20', 'way/21', 'relation/30'}
    assert by_id['node/1']['name'] == '버스정류장'
    assert by_id['node/1']['nameIsFallback'] is True
    assert by_id['node/2']['type'] == 'school'
    assert by_id['node/3']['type'] == 'elementary'
    assert by_id['way/20']['positionMethod'] == 'line_midpoint'
    assert by_id['way/21']['positionMethod'] == 'area_representative_point'
    park = by_id['relation/30']
    assert not (127.01 < park['longitude'] < 127.04 and 37.51 < park['latitude'] < 37.54)
    assert counters['outsideScope'] == 1
    assert counters['included'] == 6


def test_real_scope_manifest_integrity_and_exact_codes():
    scope, metadata = poi.load_scope(poi.ROOT / 'src/data/region-selection.json')
    assert metadata['regionCodes'] == list(poi.SCOPE_CODES)
    assert metadata['coverageComplete'] is False
    assert scope.region(Point(127.03, 37.50)) == '11'
    assert scope.region(Point(129.07, 35.18)) == '21'
    assert scope.region(Point(128.60, 35.87)) is None
