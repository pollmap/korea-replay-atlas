from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import xml.etree.ElementTree as ET

import pytest

from pipeline.real_estate import (RealEstateError, build_partitions, canonical_bytes,
    normalize_xml_page, sha256, summarize_transactions, write_dataset)


RETRIEVED = '2026-09-20T00:00:00Z'


def row(**changes):
    return {'sggCd': '11110', 'umdCd': '17500', 'umdNm': '가상동', 'aptNm': '검증용 가상단지',
        'aptSeq': '11110-999999', 'jibun': '123-4', 'excluUseAr': '84.9900',
        'dealYear': '2026', 'dealMonth': '9', 'dealDay': '2', 'dealAmount': ' 123,456 ',
        'floor': '12', 'buildYear': '2008', 'rgstDate': '20260918', 'cdealType': '',
        'cdealDay': '', **changes}


def xml(rows, *, page_no=1, page_size=100, total=None, code='000'):
    root = ET.Element('response')
    header = ET.SubElement(root, 'header')
    ET.SubElement(header, 'resultCode').text = code
    ET.SubElement(header, 'resultMsg').text = 'OK'
    body = ET.SubElement(root, 'body')
    items = ET.SubElement(body, 'items')
    for source in rows:
        item = ET.SubElement(items, 'item')
        for key, value in source.items():
            if value is not None:
                ET.SubElement(item, key).text = value
    for key, value in {'pageNo': page_no, 'numOfRows': page_size,
                       'totalCount': len(rows) if total is None else total}.items():
        ET.SubElement(body, key).text = str(value)
    return ET.tostring(root, encoding='utf-8', xml_declaration=True)


def page(rows, **metadata):
    return normalize_xml_page(xml(rows, **metadata), lawd_code='11110', deal_month='202609', retrieved_at=RETRIEVED)


def snapshot(rows):
    return build_partitions([page(rows)])[0]


def summary(records, **changes):
    return summarize_transactions(records, complex_id='molit-apt:11110:11110-999999',
        area_m2='84.99', date_from='2026-09-01', date_to='2026-09-30', **changes)


def test_integer_won_exact_area_and_date_semantics():
    raw = xml([row()])
    record = normalize_xml_page(raw, lawd_code='11110', deal_month='202609', retrieved_at=RETRIEVED)['records'][0]
    assert record['price_krw'] == 1_234_560_000
    assert record['area_m2'] == '84.99'
    assert record['contract_date'] == '2026-09-02'
    assert record['registration_date'] == '2026-09-18'
    assert record['reported_at'] is record['source_updated_at'] is record['position'] is None
    assert record['provenance']['observed_at'] is None
    assert record['provenance']['evidence_type'] == 'official_report'
    assert record['provenance']['input_sha256'] == sha256(raw)
    assert record['legal_dong_code'] == '1111017500'


@pytest.mark.parametrize('value,code', [('', 'missing'), ('0', 'out_of_range'),
    ('-100', 'invalid_format'), ('12,34', 'invalid_format'), ('1.5', 'invalid_format'),
    ('1e6', 'invalid_format'), ('900719925475', 'out_of_range')])
def test_invalid_prices_are_not_zero_or_rounded(value, code):
    record = snapshot([row(dealAmount=value)])['records'][0]
    assert record['price_krw'] is None
    assert {'field': 'dealAmount', 'code': code} in record['issues']
    assert summary([record])['count'] == 0


@pytest.mark.parametrize('changes,field,code', [
    ({'excluUseAr': ''}, 'excluUseAr', 'missing'),
    ({'excluUseAr': '84.9900001'}, 'excluUseAr', 'invalid_format'),
    ({'excluUseAr': '0'}, 'excluUseAr', 'out_of_range'),
    ({'floor': ''}, 'floor', 'missing'), ({'floor': '12층'}, 'floor', 'invalid_format'),
    ({'buildYear': '2027'}, 'buildYear', 'out_of_range'),
    ({'umdCd': '175'}, 'umdCd', 'invalid_format'),
    ({'jibun': ''}, 'jibun', 'missing'), ({'jibun': '가상로 12'}, 'jibun', 'invalid_format'),
    ({'aptSeq': None}, 'aptSeq', 'missing'),
    ({'sggCd': '26110'}, 'sggCd', 'scope_mismatch'),
    ({'dealDay': '31'}, 'contract_date', 'invalid_format'),
    ({'dealMonth': '8'}, 'contract_date', 'scope_mismatch'),
    ({'dealDay': '25'}, 'contract_date', 'out_of_range'),
])
def test_field_quality_distinguishes_missing_invalid_and_scope(changes, field, code):
    record = snapshot([row(**changes)])['records'][0]
    assert {'field': field, 'code': code} in record['issues']
    assert record['quality'] == ('incomplete' if code == 'missing' else 'invalid')


def test_same_name_different_ids_and_missing_id_never_merge():
    records = snapshot([row(), row(aptSeq='11110-999998'), row(aptSeq=None)])['records']
    assert len({r['complex_id'] for r in records}) == 3
    assert sum(r['complex_id'] is None for r in records) == 1
    assert summary(records)['count'] == 1


def test_cancelled_and_unrecognized_flags_excluded_by_default():
    records = snapshot([row(dealAmount='10,000'), row(dealAmount='20,000', cdealType='O', cdealDay='26.09.10'),
        row(dealAmount='30,000', cdealType='Y'), row(dealAmount='40,000', cdealType='unexpected'),
        row(dealAmount='50,000', cdealDay='invalid')])['records']
    assert summary(records)['median_price_krw'] == 100_000_000
    assert summary(records)['count'] == 1
    assert sum(r['cancellation']['status'] == 'cancelled' for r in records) == 3
    cancelled = next(r for r in records if r['cancellation']['source_flag'] == 'O')
    assert cancelled['cancellation']['reason_date'] == '2026-09-10'


def test_registration_date_does_not_create_report_date_and_short_year_is_bounded():
    records = snapshot([row(rgstDate='26.09.18.'), row(rgstDate='25.09.18'), row(rgstDate='27.09.18')])['records']
    assert sum(r['registration_date'] == '2026-09-18' for r in records) == 1
    assert all(r['reported_at'] is None for r in records)
    assert sum(r['quality'] == 'invalid' for r in records) == 2


def test_median_uses_same_area_period_and_preserves_identical_report_rows():
    result = snapshot([row(dealAmount='10,000'), row(dealAmount='30,000'), row(dealAmount='30,000'),
        row(dealAmount='999,999', excluUseAr='59.99'), row(dealAmount='1,000', aptSeq='11110-999998')])
    assert result['audit']['identical_row_occurrences'] == 1
    assert len({r['id'] for r in result['records']}) == 5
    assert summary(result['records'])['count'] == 3
    assert summary(result['records'])['median_price_krw'] == 300_000_000
    assert summary([])['median_price_krw'] is None
    pair = snapshot([row(dealAmount='10,001'), row(dealAmount='10,002')])['records']
    assert summary(pair)['median_price_krw'] == 100_015_000
    with pytest.raises(RealEstateError, match='duplicate_snapshot_records'):
        summary([pair[0], pair[0]])


def test_order_and_repeat_processing_deterministic_without_dropping_real_duplicates():
    first = page([row()], page_no=1, page_size=1, total=2)
    second = page([row()], page_no=2, page_size=1, total=2)
    expected = canonical_bytes(build_partitions([first, second]))
    assert canonical_bytes(build_partitions([second, first, first])) == expected
    assert len(build_partitions([first, second])[0]['records']) == 2
    with pytest.raises(RealEstateError, match='missing_page'):
        build_partitions([first])
    conflicting = page([row(dealAmount='1')], page_no=1, page_size=1, total=2)
    with pytest.raises(RealEstateError, match='conflicting_page'):
        build_partitions([first, conflicting, second])
    later = deepcopy(second)
    later['retrieved_at'] = '2026-09-20T01:00:00Z'
    with pytest.raises(RealEstateError, match='mixed_snapshot_window'):
        build_partitions([first, later])


def test_empty_is_valid_only_with_complete_success_envelope():
    result = snapshot([])
    assert result['records'] == [] and result['audit']['complete_pages']
    with pytest.raises(RealEstateError, match='page_count_mismatch'):
        page([], total=1)
    with pytest.raises(RealEstateError, match='upstream_auth'):
        page([], code='30')
    with pytest.raises(RealEstateError, match='upstream_quota'):
        page([], code='22')
    with pytest.raises(RealEstateError, match='missing_body'):
        normalize_xml_page(b'<response><header><resultCode>000</resultCode></header></response>',
            lawd_code='11110', deal_month='202609', retrieved_at=RETRIEVED)


@pytest.mark.parametrize('raw,expected', [
    (b'<!DOCTYPE response [<!ENTITY a "secret">]><response>&a;</response>', 'unsafe_xml'),
    (b'<response><header/><header/></response>', 'duplicate_xml_field'),
    (b'<broken>', 'invalid_xml'),
    ('<!DOCTYPE response [<!ENTITY a "secret">]><response>&a;</response>'.encode('utf-16-le'), 'invalid_xml_encoding'),
    (b'<response xmlns="https://example.invalid/"><header/></response>', 'invalid_envelope'),
    (b'<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode>'
     b'<errMsg>do-not-log-this-value</errMsg></cmmMsgHeader></OpenAPI_ServiceResponse>', 'upstream_auth'),
])
def test_xml_structure_security_and_sanitized_errors(raw, expected):
    with pytest.raises(RealEstateError) as error:
        normalize_xml_page(raw, lawd_code='11110', deal_month='202609', retrieved_at=RETRIEVED)
    assert str(error.value) == expected


@pytest.mark.parametrize('lawd,month,instant', [('1111', '202609', RETRIEVED),
    ('../xx', '202609', RETRIEVED), ('11110', '202613', RETRIEVED),
    ('11110', '202609', '2026-09-20'), ('11110', '202609', '2026-02-30T00:00:00Z')])
def test_scope_and_retrieval_inputs_rejected(lawd, month, instant):
    with pytest.raises(RealEstateError):
        normalize_xml_page(xml([]), lawd_code=lawd, deal_month=month, retrieved_at=instant)


def test_writer_creates_shards_and_hash_manifest_without_public_or_overwrite(tmp_path):
    partition = snapshot([row()])
    source = tmp_path / 'source.xml'
    source.write_bytes(xml([row()]))
    before = source.read_bytes()
    output = tmp_path / 'normalized'
    manifest = write_dataset([partition], output)
    entry = manifest['partitions'][0]
    assert entry['path'] == '11110/202609.json'
    payload = (output / entry['path']).read_bytes()
    assert sha256(payload) == entry['sha256'] and len(payload) == entry['bytes']
    assert json.loads(payload)['records'][0]['position'] is None
    assert source.read_bytes() == before
    assert 'serviceKey' not in (output / 'manifest.json').read_text(encoding='utf-8')
    with pytest.raises(RealEstateError, match='output_exists'):
        write_dataset([partition], output)
    with pytest.raises(RealEstateError, match='public_output_forbidden'):
        write_dataset([partition], tmp_path / 'public' / 'real-estate')
    alternate = tmp_path / 'again'
    write_dataset([partition], alternate)
    assert (output / 'manifest.json').read_bytes() == (alternate / 'manifest.json').read_bytes()


def test_writer_failure_does_not_expose_final_directory(tmp_path, monkeypatch):
    original = Path.open
    def fail_manifest(self, *args, **kwargs):
        if self.name == 'manifest.json' and args and args[0] == 'xb':
            raise OSError('fixture write interruption')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, 'open', fail_manifest)
    with pytest.raises(OSError):
        write_dataset([snapshot([row()])], tmp_path / 'final')
    assert not (tmp_path / 'final').exists()


def test_regions_and_months_split_without_name_merging_and_keep_kst_date():
    first = page([row()])
    earlier = normalize_xml_page(xml([row(dealMonth='8')]), lawd_code='11110', deal_month='202608', retrieved_at=RETRIEVED)
    other = normalize_xml_page(xml([row(sggCd='26110', aptSeq='26110-999999', dealDay='20', rgstDate='')]),
        lawd_code='26110', deal_month='202609', retrieved_at='2026-09-19T15:00:00Z')
    partitions = build_partitions([other, first, earlier])
    assert [(p['lawd_code'], p['deal_month']) for p in partitions] == [('11110', '202608'), ('11110', '202609'), ('26110', '202609')]
    assert partitions[-1]['records'][0]['quality'] == 'valid'


@pytest.mark.parametrize('changes', [{'area_m2': '84.9900'}, {'area_m2': '0'},
    {'date_from': '20260901'}, {'date_from': '2026-10-01'}, {'complex_id': '단지명'}])
def test_summary_requires_unambiguous_canonical_filters(changes):
    filters = {'complex_id': 'molit-apt:11110:11110-999999', 'area_m2': '84.99',
               'date_from': '2026-09-01', 'date_to': '2026-09-30', **changes}
    with pytest.raises(RealEstateError):
        summarize_transactions([], **filters)


def test_offline_cli_and_source_path_boundary(tmp_path):
    (tmp_path / 'page.xml').write_bytes(xml([row()]))
    descriptor = {'schema_version': 1, 'pages': [{'path': 'page.xml', 'lawd_code': '11110',
        'deal_month': '202609', 'retrieved_at': RETRIEVED}]}
    path = tmp_path / 'input.json'
    path.write_text(json.dumps(descriptor), encoding='utf-8')
    command = [sys.executable, '-B', '-m', 'pipeline.real_estate', '--manifest', str(path), '--output']
    valid = subprocess.run([*command, str(tmp_path / 'output')], capture_output=True, text=True, timeout=15)
    assert valid.returncode == 0
    assert json.loads(valid.stdout) == {'status': 'verified', 'partitions': 1, 'records': 1}
    assert (tmp_path / 'output' / '11110' / '202609.json').is_file()
    descriptor['pages'][0]['path'] = '../outside.xml'
    path.write_text(json.dumps(descriptor), encoding='utf-8')
    invalid = subprocess.run([*command, str(tmp_path / 'not-created')], capture_output=True, text=True, timeout=15)
    assert invalid.returncode == 1 and 'invalid_source_path' in invalid.stderr
    assert not (tmp_path / 'not-created').exists()
