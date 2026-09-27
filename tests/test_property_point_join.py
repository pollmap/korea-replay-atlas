import hashlib
import json
from pathlib import Path
import sqlite3

import pytest

from pipeline.property_point_join import (
    _release_sale_window,
    _sale_rows,
    match_identities,
    recent_sale_markers,
)


def seoul(code='A12345678', name='헬리오시티아파트', number='345'):
    return {'APT_CD': code, 'APT_NM': name, 'CTPV_ADDR': '서울',
            'SGG_ADDR': '송파구', 'RDN_ADDR': '송파대로',
            'ROAD_DADDR': number,
            'APT_RDN_ADDR': f'서울특별시 송파구 송파대로 {number}'}


def sale(seq='11710-8865', name='헬리오시티', number='00345'):
    return {'sggCd': '11710', 'aptSeq': seq, 'aptNm': name,
            'roadNmSggCd': '11710', 'roadNm': '송파대로',
            'roadNmBonbun': number, 'roadNmBubun': '00000'}


def match(rows, sales, known=None):
    return match_identities(rows, [('11710', item) for item in sales],
                            {'송파구': '11710'},
                            {'molit-apt:11710:11710-8865'} if known is None else known)


def test_unique_official_road_address_and_name_links_helio_despite_different_lot():
    matches, audit = match([seoul()], [sale()])
    assert matches == {'A12345678': 'molit-apt:11710:11710-8865'}
    assert audit['matched'] == 1


def test_ambiguous_or_conflicting_road_address_never_links():
    assert match([seoul(), seoul(code='B12345678')], [sale()])[0] == {}
    assert match([seoul()], [sale(), sale(seq='11710-123')])[0] == {}
    assert match([seoul()], [sale(), sale(number='00347')])[0] == {}
    assert match([seoul(name='다른 아파트')], [sale()])[0] == {}
    assert match([seoul()], [sale()], set())[0] == {}
    assert match([seoul(number='345')], [sale(number='00346')])[0] == {}


def test_published_point_asset_is_pinned_and_keeps_geometry_quality_separate():
    path = Path(__file__).resolve().parents[1] / 'src/data/seoul-kapt-points-33058dae0a1d86c3.geojson'
    body = path.read_bytes()
    assert hashlib.sha256(body).hexdigest() == '33058dae0a1d86c302b2f1c5b0dff9d71241a60031880f4f738c9fe506611792'
    features = json.loads(body)['features']
    linked = [row for row in features if 'property_complex_id' in row['properties']]
    assert len(features) == 2780 and len(linked) == 848
    assert all(row['properties']['coordinate_status'] == 'provider_xy_crs_unconfirmed' for row in linked)
    assert next(row for row in linked if row['id'] == 'A10025850')['properties']['property_complex_id'] == 'molit-apt:11710:11710-8865'
    assert b'TELNO' not in body and b'phone' not in body and b'price_krw' not in body


def test_recent_sale_map_labels_are_actual_reports_with_area_and_date():
    path = Path(__file__).resolve().parents[1] / 'src/data/seoul-kapt-points-b63b62af834062de.geojson'
    body = path.read_bytes()
    assert hashlib.sha256(body).hexdigest() == 'b63b62af834062de98b142f4caef4f8a2087bd8e713c15ba3351fcf3dc06859c'
    points = json.loads(body)['features']
    priced = [row for row in points if 'recent_sale_label' in row['properties']]
    assert len(points) == 2780 and len(priced) == 847
    assert all('property_complex_id' in row['properties'] and row['properties']['coordinate_status'] == 'provider_xy_crs_unconfirmed' for row in priced)
    helio = next(row for row in priced if row['id'] == 'A10025850')['properties']
    assert (helio['recent_sale_price_krw'], helio['recent_sale_area_m2'], helio['recent_sale_contract_date']) == (2900000000, '84.99', '2026-08-15')
    assert helio['recent_sale_label'] == '최근 신고 29억 · 84.99㎡'
    assert b'TELNO' not in body and b'phone' not in body


SEOUL_CODES = [f'11{district:03d}' for district in range(100, 125)]
MARKER_ID = 'molit-apt:11100:11100-1'


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(value, ensure_ascii=False).encode('utf-8')
    path.write_bytes(body)
    return {'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}


def marker_release(tmp_path, *, end='202609', months=None, missing=False, duplicate=False,
                   priced_months=('202509', '202609')):
    root = tmp_path / 'property-window-test'
    period = {'from': '202401', 'to': '202612', 'latest_complete_month': end}
    save_json(root / 'manifest.json', {'kind': 'property-release', 'release_id': root.name, 'period': period})
    months = months or ['202510', '202511', '202512', '202601', '202602', '202603',
                        '202604', '202605', '202606', '202607', '202608', '202609']
    regions = []
    for code in SEOUL_CODES:
        parts = []
        for month in months:
            rows = []
            if code == SEOUL_CODES[0] and month in priced_months:
                rows = [{'id': f'trade-{month}', 'complex_id': MARKER_ID, 'trade_type': 'sale',
                         'quality': 'valid', 'statistics_eligible': True, 'cancellation': 'not_reported',
                         'price_krw': 900_000_000 if month == '202509' else 600_000_000,
                         'contract_date': f'{month[:4]}-{month[4:]}-15', 'area_m2': '84.99'}]
            filename = f'transactions/{code}/{month}-000.json'
            ref = save_json(root / filename, {'release_id': root.name, 'lawd_code': code,
                                             'deal_month': month, 'transactions': rows})
            parts.append({'trade_type': 'sale', 'deal_month': month, 'status': 'complete',
                          'source_rows': len(rows), 'transactions': [{**ref, 'url': f'/data/property/{root.name}/{filename}'}]})
        if code == SEOUL_CODES[0] and missing:
            parts.pop()
        if code == SEOUL_CODES[0] and duplicate:
            parts[-1] = parts[0]
        index = save_json(root / 'regions' / f'{code}.json', {
            'release_id': root.name, 'lawd_code': code, 'period': period, 'partitions': parts})
        regions.append({'lawd_code': code, 'index': index})
    save_json(root / 'regions.json', {'release_id': root.name, 'regions': regions})
    return root


def test_marker_window_moves_with_manifest_and_drops_expired_higher_price(tmp_path):
    months = ['202509', '202510', '202511', '202512', '202601', '202602', '202603',
              '202604', '202605', '202606', '202607', '202608', '202609']
    root = marker_release(tmp_path, months=months)
    marker = recent_sale_markers(root, {MARKER_ID})[MARKER_ID]
    assert marker == {'contract_date': '2026-09-15', 'source_transaction_id': 'trade-202609',
                      'price_krw': 600_000_000, 'area_m2': '84.99'}
    # Same release contents, prior completed month: the older report is still eligible.
    prior = marker_release(tmp_path / 'prior', end='202608', months=months)
    marker = recent_sale_markers(prior, {MARKER_ID})[MARKER_ID]
    assert marker['contract_date'] == '2025-09-15' and marker['price_krw'] == 900_000_000


def test_expired_only_report_is_not_retained_as_current_price(tmp_path):
    months = ['202509', '202510', '202511', '202512', '202601', '202602', '202603',
              '202604', '202605', '202606', '202607', '202608', '202609']
    root = marker_release(tmp_path, months=months, priced_months=('202509',))
    assert recent_sale_markers(root, {MARKER_ID}) == {}


@pytest.mark.parametrize('options', [{'missing': True}, {'duplicate': True}])
def test_each_district_requires_twelve_distinct_completed_months(tmp_path, options):
    root = marker_release(tmp_path, **options)
    with pytest.raises(ValueError, match='incomplete_sale_marker_window'):
        recent_sale_markers(root, {MARKER_ID})


@pytest.mark.parametrize(('end', 'start'), [('202608', '202509'), ('202601', '202502'), ('202612', '202601')])
def test_release_window_handles_year_rollover(tmp_path, end, start):
    root = tmp_path / 'property-window-test'
    save_json(root / 'manifest.json', {'kind': 'property-release', 'release_id': root.name,
                                     'period': {'from': '202001', 'to': '202612', 'latest_complete_month': end}})
    months = _release_sale_window(root)
    assert len(months) == len(set(months)) == 12
    assert (months[0], months[-1]) == (start, end)


@pytest.mark.parametrize('end', [None, 202608, '202613', '202600', '2026-08', '202608extra'])
def test_invalid_release_completed_month_fails_closed(tmp_path, end):
    root = tmp_path / 'property-window-test'
    save_json(root / 'manifest.json', {'kind': 'property-release', 'release_id': root.name,
                                     'period': {'from': '202001', 'to': '202612', 'latest_complete_month': end}})
    with pytest.raises(ValueError, match='unexpected_property_release_window'):
        _release_sale_window(root)


@pytest.mark.parametrize('period', [
    {'from': '202601', 'to': '202609', 'latest_complete_month': '202609'},
    {'from': '202401', 'to': '202608', 'latest_complete_month': '202609'},
])
def test_manifest_must_cover_entire_window(tmp_path, period):
    root = tmp_path / 'property-window-test'
    save_json(root / 'manifest.json', {'kind': 'property-release', 'release_id': root.name, 'period': period})
    with pytest.raises(ValueError, match='incomplete_sale_marker_window'):
        _release_sale_window(root)


def test_mismatched_region_period_cannot_reuse_stale_markers(tmp_path):
    root = marker_release(tmp_path)
    manifest = json.loads((root / 'manifest.json').read_text(encoding='utf-8'))
    manifest['period']['latest_complete_month'] = '202610'
    save_json(root / 'manifest.json', manifest)
    with pytest.raises(ValueError, match='unexpected_property_release_window'):
        recent_sale_markers(root, {MARKER_ID})


def checkpoint_jobs(tmp_path, jobs):
    with sqlite3.connect(tmp_path / 'checkpoint.sqlite') as db:
        db.execute('CREATE TABLE jobs (lawd_code TEXT, deal_month TEXT, trade_type TEXT, status TEXT, pages TEXT, snapshot TEXT)')
        db.executemany('INSERT INTO jobs VALUES (?, ?, ?, ?, ?, ?)', [(*job, None) for job in jobs])


def test_raw_sale_window_rejects_missing_month_even_when_job_count_is_300(tmp_path):
    months = ['202510', '202511', '202512', '202601', '202602', '202603',
              '202604', '202605', '202606', '202607', '202608', '202609']
    jobs = [(code, month, 'sale', 'complete', '[]') for code in SEOUL_CODES for month in months]
    jobs[-1] = jobs[-2]
    checkpoint_jobs(tmp_path, jobs)
    with pytest.raises(ValueError, match='incomplete_seoul_sale_window'):
        _sale_rows(tmp_path, start='202510', end='202609', district_codes=set(SEOUL_CODES))


def test_raw_sale_window_uses_same_rolling_year_and_ignores_old_jobs(tmp_path):
    months = ['202509', '202510', '202511', '202512', '202601', '202602', '202603',
              '202604', '202605', '202606', '202607', '202608', '202609']
    jobs = [(code, month, 'sale', 'failed' if month == '202509' else 'complete', '[]')
            for code in SEOUL_CODES for month in months]
    checkpoint_jobs(tmp_path, jobs)
    assert _sale_rows(tmp_path, start='202510', end='202609', district_codes=set(SEOUL_CODES)) == ([], [])


def retained_checkpoint(tmp_path, status, *, empty=False):
    from pipeline.property_point_join import _twelve_months
    from pipeline.real_estate import build_partitions, canonical_bytes, normalize_xml_page
    from test_real_estate_fetch import xml, STAMP

    months = _twelve_months('202609')
    checkpoint_jobs(tmp_path, [(code, month, 'sale', 'complete', '[]')
                              for code in SEOUL_CODES for month in months])
    item = {**sale(seq='11110-99999'), 'sggCd': '11110', 'roadNmSggCd': '11110',
            'umdNm': '검증동', 'jibun': '1-2', 'excluUseAr': '84.99',
            'dealYear': '2026', 'dealMonth': '9', 'dealDay': '1',
            'dealAmount': '20,000', 'floor': '4', 'buildYear': '2000'}
    body = xml([] if empty else [item])
    digest = hashlib.sha256(body).hexdigest()
    raw = tmp_path / f'raw/sale/11110/202609/{digest}.xml'
    raw.parent.mkdir(parents=True)
    raw.write_bytes(body)
    partition = build_partitions([normalize_xml_page(body, lawd_code='11110', deal_month='202609',
                                                    retrieved_at=STAMP, trade_type='sale')])[0]
    recorded = canonical_bytes(partition)
    snapshot = tmp_path / 'snapshots/retained.json'
    snapshot.parent.mkdir()
    snapshot.write_bytes(recorded)
    descriptor = {'path': 'snapshots/retained.json', 'bytes': len(recorded),
                  'sha256': hashlib.sha256(recorded).hexdigest()}
    with sqlite3.connect(tmp_path / 'checkpoint.sqlite') as db:
        # Deliberately unusable refresh pages must never replace retained evidence.
        db.execute("UPDATE jobs SET status=?,pages=?,snapshot=? WHERE lawd_code='11110' AND deal_month='202609'",
                   (status, '[{"path":"refresh-must-not-be-read.xml"}]', json.dumps(descriptor)))
        original = list(db.execute('SELECT * FROM jobs'))
    return raw, digest, item, original


@pytest.mark.parametrize('status', ['pending', 'partial', 'failed'])
def test_retained_verified_snapshot_preserves_address_rows_without_changing_ledger(tmp_path, status):
    _, digest, item, original = retained_checkpoint(tmp_path, status)
    assert _sale_rows(tmp_path, start='202510', end='202609', district_codes=set(SEOUL_CODES)) == (
        [('11110', item)], [digest])
    with sqlite3.connect(tmp_path / 'checkpoint.sqlite') as db:
        assert list(db.execute('SELECT * FROM jobs')) == original


@pytest.mark.parametrize('status', ['empty', 'pending', 'partial', 'failed'])
def test_verified_empty_snapshot_is_observed_empty_not_missing_month(tmp_path, status):
    _, digest, _, _ = retained_checkpoint(tmp_path, status, empty=True)
    if status == 'empty':
        # A completed empty observation uses its completed page descriptors.
        from pipeline.real_estate_publish import snapshot_sources
        with sqlite3.connect(tmp_path / 'checkpoint.sqlite') as db:
            sources = snapshot_sources({'trade_type': 'sale', 'lawd_code': '11110', 'deal_month': '202609'},
                                       (tmp_path / 'snapshots/retained.json').read_bytes())
            db.execute("UPDATE jobs SET pages=? WHERE status='empty'", (json.dumps(sources),))
    assert _sale_rows(tmp_path, start='202510', end='202609', district_codes=set(SEOUL_CODES)) == ([], [digest])


def test_retained_snapshot_requires_original_hash(tmp_path):
    raw, _, _, _ = retained_checkpoint(tmp_path, 'partial')
    raw.write_bytes(raw.read_bytes().replace(b'20,000', b'21,000'))
    with pytest.raises(ValueError, match='checkpoint_hash_mismatch'):
        _sale_rows(tmp_path, start='202510', end='202609', district_codes=set(SEOUL_CODES))


def test_retained_snapshot_must_reproduce_even_if_snapshot_hash_matches(tmp_path):
    from pipeline.real_estate import canonical_bytes

    retained_checkpoint(tmp_path, 'pending')
    snapshot = tmp_path / 'snapshots/retained.json'
    value = json.loads(snapshot.read_bytes())
    value['retrieved_at'] = '2026-09-21T00:00:00Z'
    recorded = canonical_bytes(value)
    snapshot.write_bytes(recorded)
    descriptor = {'path': 'snapshots/retained.json', 'bytes': len(recorded),
                  'sha256': hashlib.sha256(recorded).hexdigest()}
    with sqlite3.connect(tmp_path / 'checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET snapshot=? WHERE status='pending'", (json.dumps(descriptor),))
    with pytest.raises(ValueError, match='snapshot_not_reproducible'):
        _sale_rows(tmp_path, start='202510', end='202609', district_codes=set(SEOUL_CODES))


def test_unverified_retained_month_cannot_complete_twelve_month_window(tmp_path):
    retained_checkpoint(tmp_path, 'pending')
    with sqlite3.connect(tmp_path / 'checkpoint.sqlite') as db:
        db.execute("UPDATE jobs SET snapshot=NULL WHERE status='pending'")
    with pytest.raises(ValueError, match='incomplete_seoul_sale_window'):
        _sale_rows(tmp_path, start='202510', end='202609', district_codes=set(SEOUL_CODES))
