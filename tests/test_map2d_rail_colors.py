import unittest
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import zlib

from pipeline.map2d_rail_colors import build, seoul_line, suin_bundang_line, daejeon_line


class RailIdentityTests(unittest.TestCase):
    def test_daejeon_requires_its_network_operator_and_operating_line_1(self):
        row={'type':'route','route':'subway','name':'대전 도시철도 1호선: 판암 → 반석',
             'network':'대전 도시철도','ref':'1','operator':'대전교통공사'}
        self.assertEqual(daejeon_line(row),'daejeon-1')
        for changed in ({'name':'대전 도시철도 2호선: 충대농대 → 탑립'},{'operator':'unknown'},
                        {'network':'수도권 전철'},{'ref':'2'},{'construction':'yes'},
                        {'proposed':'yes'},{'route':'train'},{'name':'1호선'}):
            self.assertIsNone(daejeon_line({**row,**changed}))

    def test_suin_bundang_requires_operator_network_reference_and_operating_route(self):
        row = {'type':'route','route':'train','name':'수인·분당선: 왕십리 → 고색','ref':'수인·분당',
               'network':'수도권 전철','operator':'한국철도공사'}
        self.assertEqual(suin_bundang_line(row),'suin-bundang')
        self.assertEqual(suin_bundang_line({**row,'name':'수인·분당선 급행: 왕십리 → 고색'}),'suin-bundang')
        for changed in ({'operator':'unknown'},{'network':'부산 도시철도'},{'ref':'분당'},
                        {'name':'분당선'},{'construction':'yes'},{'state':'proposed'},{'route':'railway'}):
            self.assertIsNone(suin_bundang_line({**row,**changed}))

    def test_suin_bundang_excludes_other_operating_routes_on_the_same_way(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);source=root/'source.osm'
            source.write_text('''<osm version="0.6">
<way id="1"><tag k="railway" v="rail"/></way><way id="2"><tag k="railway" v="rail"/></way>
<relation id="100"><member type="way" ref="1" role=""/><member type="way" ref="2" role=""/>
<tag k="type" v="route"/><tag k="route" v="train"/><tag k="name" v="수인·분당선: 왕십리 → 인천"/>
<tag k="ref" v="수인·분당"/><tag k="network" v="수도권 전철"/><tag k="operator" v="한국철도공사"/></relation>
<relation id="101"><member type="way" ref="1" role=""/><tag k="type" v="route"/><tag k="route" v="train"/>
<tag k="name" v="수인·분당선 급행: 왕십리 → 고색"/><tag k="ref" v="수인·분당"/>
<tag k="network" v="수도권 전철"/><tag k="operator" v="한국철도공사"/></relation>
<relation id="102"><member type="way" ref="2" role=""/><tag k="type" v="route"/><tag k="route" v="subway"/>
<tag k="name" v="수도권 전철 4호선: 오이도 → 불암산"/><tag k="ref" v="4"/><tag k="network" v="수도권 전철"/></relation>
</osm>''',encoding='utf-8')
            index=root/'index.sqlite'
            with closing(sqlite3.connect(index)) as db:
                db.execute('CREATE TABLE records(topic TEXT,stable TEXT,record BLOB)')
                for identity in (1,2):
                    row={'source_id':'osm','source_record_id':f'way/{identity}','properties':{'railway':'rail'}}
                    db.execute('INSERT INTO records VALUES(?,?,?)',('rail',str(identity)*64,zlib.compress(json.dumps(row).encode())))
                db.commit()
            result=build(source,[index])
            self.assertEqual(result['records'],{'1'*16:{'source_record_id':'way/1','line':'suin-bundang'}})

    def test_service_labels_require_explicit_source_tag_and_matching_osm_id(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'source.osm'
            source.write_text('''<osm version="0.6">
<way id="379994149"><tag k="railway" v="subway"/><tag k="service" v="crossover"/><tag k="name" v="9호선"/></way>
<way id="1116508685"><tag k="railway" v="subway"/><tag k="service" v="crossover"/><tag k="name" v="9호선"/></way>
<way id="10"><tag k="railway" v="rail"/><tag k="service" v="siding"/></way>
<way id="11"><tag k="railway" v="rail"/><tag k="service" v="yard"/></way>
<way id="12"><tag k="railway" v="rail"/><tag k="name" v="경부선"/></way>
<way id="13"><tag k="railway" v="rail"/><tag k="service" v="spur"/></way>
<way id="14"><tag k="highway" v="service"/><tag k="service" v="yard"/></way>
</osm>''', encoding='utf-8')
            index = root / 'index.sqlite'
            with closing(sqlite3.connect(index)) as db:
                db.execute('CREATE TABLE records(topic TEXT,stable TEXT,record BLOB)')
                for ordinal, identity in enumerate((379994149,1116508685,10,11,12,13,14)):
                    record = {'source_id':'osm','source_record_id':f'way/{identity}','properties':{'railway':'rail'}}
                    db.execute('INSERT INTO records VALUES(?,?,?)',('rail',f'{ordinal:016x}'+'0'*48,zlib.compress(json.dumps(record).encode())))
                foreign = {'source_id':'other','source_record_id':'way/379994149','properties':{'railway':'subway'}}
                db.execute('INSERT INTO records VALUES(?,?,?)',('rail','f'*64,zlib.compress(json.dumps(foreign).encode())))
                db.commit()
            result = build(source,[index])
            self.assertEqual(len(result['suppressed_labels']),4)
            self.assertEqual({v['source_record_id'] for v in result['suppressed_labels'].values()},
                             {'way/379994149','way/1116508685','way/10','way/11'})
            self.assertEqual(result['records'],{})

    def test_explicit_operating_seoul_route(self):
        row = {'type': 'route', 'route': 'subway', 'name': '서울 지하철 2호선: 내선순환',
               'ref': '2', 'network': '수도권 전철'}
        self.assertEqual(seoul_line(row), '2')
        for changed in ({'name': '2호선'}, {'name': '부산 도시철도 2호선'}, {'ref': '3'},
                        {'network': '부산 도시철도'}, {'construction': 'yes'},
                        {'state': 'in_progress'}, {'type': 'route_master'}):
            self.assertIsNone(seoul_line({**row, **changed}))


if __name__ == '__main__':
    unittest.main()
