from copy import deepcopy
import json

import pytest

from pipeline.real_estate import RealEstateError, canonical_bytes, sha256
from pipeline.real_estate_complex_summary import build, summarize_rows
from pipeline.real_estate_publish import publish
from test_real_estate_fetch import registry
from test_real_estate_publish import setup


def row(identity='test-1', area='84.99', trade='sale', **changes):
    return {'id': identity, 'complex_id': 'molit-apt:11110:11110-99999',
        'trade_type': trade, 'area_m2': area, 'contract_date': '2026-09-01',
        'retrieved_at': '2026-09-15T01:00:00Z', 'statistics_eligible': True,
        'price_krw': 200_000_000 if trade == 'sale' else None,
        'deposit_krw': 100_000_000 if trade == 'rent' else None,
        'monthly_rent_krw': 0 if trade == 'rent' else None, **changes}


def test_exact_area_counts_occurrences_and_latest_contract_not_collection_order():
    rows, audit = summarize_rows([row('1', '84.990'), row('2'),
        row('3', contract_date='2026-09-02', retrieved_at='2026-09-03T01:00:00Z', price_krw=210_000_000),
        row('4', area='85'), row('5', statistics_eligible=False)])
    assert len(rows) == 2 and rows[0]['area_m2'] == '84.99'
    assert rows[0]['transaction_count'] == 3
    assert rows[0]['latest_price_krw'] == 210_000_000
    assert rows[0]['median_price_krw'] == 200_000_000
    assert rows[1]['area_m2'] == '85'
    assert audit == {'source_rows': 5, 'eligible_rows': 4, 'grouped_rows': 4,
                     'unlinked_eligible_rows': 0, 'excluded_rows': 1, 'unknown_rent_rows': 0}


def test_jeonse_monthly_missing_money_and_unlinked_identity_stay_distinct():
    rows, audit = summarize_rows([row('1', trade='rent'), row('2', trade='rent', monthly_rent_krw=500_000),
        row('3', trade='rent', monthly_rent_krw=None), row('4', complex_id=None)])
    assert {entry['rent_kind'] for entry in rows} == {'jeonse', 'monthly'}
    assert all(entry['latest_price_krw'] is None and entry['price_basis'] == 'deposit' for entry in rows)
    assert audit['unknown_rent_rows'] == audit['unlinked_eligible_rows'] == 1
    assert audit['grouped_rows'] == 2


def test_output_is_pinned_deterministic_and_source_ids_are_audited(tmp_path):
    source = setup(tmp_path)
    publication = publish(source, registry(), tmp_path / 'candidates', reserve_bytes=0)
    path = tmp_path / 'candidates' / publication['release_id'] / 'publication.json'
    result = build(path, tmp_path / 'summary', reserve_bytes=0)
    assert result['property_release_id'] == publication['release_id']
    assert result['audit']['source_rows'] == 4 and result['audit']['grouped_rows'] == 3
    assert result['audit']['source_calls'] == result['audit']['ledger_writes'] == 0
    assert len(result['audit']['source_transaction_identity_sha256']) == 64
    assert build(path, tmp_path / 'summary', reserve_bytes=0) == result
    root = tmp_path / 'summary' / result['summary_release_id']
    region_path = next(item['path'] for item in result['files'] if '/regions/' in item['path'])
    region = json.loads((root / region_path).read_bytes())
    assert len(region['summaries']) == 1 and region['summaries'][0]['deal_month'] == '202609'
    chunk = json.loads((root / region['summaries'][0]['url'].lstrip('/')).read_bytes())
    assert sum(row['transaction_count'] for row in chunk['rows']) == 3
    complex_refs = region['complex_chunks']['molit-apt:11110:11110-99999']
    assert len(complex_refs) == 1 and complex_refs[0]['bytes'] <= 512 * 1024
    packed = json.loads((root / complex_refs[0]['url'].lstrip('/')).read_bytes())
    assert packed['kind'] == 'property-complex-summary-pack'
    assert packed['from_month'] == packed['to_month'] == '202609'
    assert packed['rows'] == chunk['rows']


def test_pending_partition_remains_null_and_no_summary(tmp_path):
    source = setup(tmp_path, 1)
    publication = publish(source, registry(), tmp_path / 'candidates', reserve_bytes=0)
    path = tmp_path / 'candidates' / publication['release_id'] / 'publication.json'
    result = build(path, tmp_path / 'summary', reserve_bytes=0)
    root = tmp_path / 'summary' / result['summary_release_id']
    region = json.loads((root / next(item['path'] for item in result['files'] if '/regions/' in item['path'])).read_bytes())
    pending = next(item for item in region['partitions'] if item['status'] == 'pending')
    assert pending['source_rows'] is pending['eligible_rows'] is None
    assert all(row['trade_type'] == 'rent' for ref in region['summaries']
               for row in json.loads((root / ref['url'].lstrip('/')).read_bytes())['rows'])


def test_changed_source_or_unaudited_publication_is_rejected(tmp_path):
    source = setup(tmp_path)
    publication = publish(source, registry(), tmp_path / 'candidates', reserve_bytes=0)
    path = tmp_path / 'candidates' / publication['release_id'] / 'publication.json'
    bad = deepcopy(publication); bad['audit']['raw_pages_reparsed'] = False
    path.write_bytes(canonical_bytes(bad))
    with pytest.raises(RealEstateError, match='summary_source_not_audited'):
        build(path, tmp_path / 'summary', reserve_bytes=0)
    path.write_bytes(canonical_bytes(publication))
    asset = next(item for item in publication['files'] if '/transactions/' in item['path'])
    (path.parent / asset['path']).write_bytes(b'changed')
    with pytest.raises(RealEstateError, match='checkpoint_size_mismatch|checkpoint_hash_mismatch'):
        build(path, tmp_path / 'summary', reserve_bytes=0)


def test_duplicate_source_transaction_id_rejected_even_if_receipt_hash_updated(tmp_path):
    source = setup(tmp_path)
    publication = publish(source, registry(), tmp_path / 'candidates', reserve_bytes=0)
    path = tmp_path / 'candidates' / publication['release_id'] / 'publication.json'
    asset = next(item for item in publication['files'] if '/transactions/' in item['path'])
    target = path.parent / asset['path']; value = json.loads(target.read_bytes())
    value['transactions'][1]['id'] = value['transactions'][0]['id']
    raw = canonical_bytes(value); target.write_bytes(raw)
    asset.update(sha256=sha256(raw), byte_length=len(raw)); path.write_bytes(canonical_bytes(publication))
    with pytest.raises(RealEstateError, match='summary_source_duplicate_identity'):
        build(path, tmp_path / 'summary', reserve_bytes=0)


@pytest.mark.parametrize('value', ['NaN', 'Infinity', '0', '-1', '1e9999'])
def test_invalid_reported_area_rejected(value):
    with pytest.raises(RealEstateError, match='summary_invalid_area'):
        summarize_rows([row(area=value)])


def test_private_intermediate_budget_does_not_bypass_cached_public_budget(tmp_path):
    source = setup(tmp_path)
    publication = publish(source, registry(), tmp_path / 'source', reserve_bytes=0)
    path = tmp_path / 'source' / publication['release_id'] / 'publication.json'
    result = build(path, tmp_path / 'summary', reserve_bytes=0, max_files=100_000)
    cached = tmp_path / 'summary' / result['summary_release_id'] / 'publication.json'
    inflated = deepcopy(result)
    inflated['files'] = [result['files'][0]] * 18_001
    cached.write_bytes(canonical_bytes(inflated))
    with pytest.raises(RealEstateError, match='summary_asset_budget'):
        build(path, tmp_path / 'summary', reserve_bytes=0)


def test_layout_byte_budget_has_a_distinct_immutable_identity(tmp_path):
    source = setup(tmp_path)
    publication = publish(source, registry(), tmp_path / 'source', reserve_bytes=0)
    path = tmp_path / 'source' / publication['release_id'] / 'publication.json'
    normal = build(path, tmp_path / 'summary', reserve_bytes=0)
    smaller = build(path, tmp_path / 'summary', reserve_bytes=0, target_bytes=3 * 1024**2)
    assert smaller['summary_release_id'] != normal['summary_release_id']


@pytest.mark.parametrize('budget', [0, 17_999, 100_001, True])
def test_invalid_private_file_budget_rejected_before_reading_source(tmp_path, budget):
    with pytest.raises(RealEstateError, match='summary_invalid_budget'):
        build(tmp_path / 'absent.json', tmp_path / 'summary', max_files=budget)
