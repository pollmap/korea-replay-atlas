import json

import pytest

from pipeline.real_estate import RealEstateError
from pipeline.real_estate_complex_summary import build as build_summary
from pipeline.real_estate_publish import publish
from pipeline.real_estate_transaction_pack import build
from test_real_estate_fetch import registry, xml, rent, STAMP, KEY
from test_real_estate_publish import setup, sale


def test_nondefault_byte_budget_creates_a_distinct_immutable_release(tmp_path):
    checkpoint = setup(tmp_path)
    source = publish(checkpoint, registry(), tmp_path / 'source', reserve_bytes=0)
    original = tmp_path / 'source' / source['release_id'] / 'publication.json'
    normal = build(original, tmp_path / 'packed', reserve_bytes=0)
    smaller = build(original, tmp_path / 'packed', reserve_bytes=0, target_bytes=3 * 1024**2)
    assert normal['release_id'] != smaller['release_id']
    assert smaller['audit']['source_transaction_identity_sha256'] == normal['audit']['source_transaction_identity_sha256']


def test_packed_release_preserves_ids_rows_states_and_legacy_row_schema(tmp_path):
    checkpoint = setup(tmp_path)
    source = publish(checkpoint, registry(), tmp_path / 'source', reserve_bytes=0)
    original = tmp_path / 'source' / source['release_id'] / 'publication.json'
    result = build(original, tmp_path / 'packed', reserve_bytes=0)
    assert result['release_id'] != source['release_id']
    assert result['audit']['all_transaction_ids_preserved'] is True
    assert result['audit']['source_rows'] == 4
    assert result['audit']['source_calls'] == result['audit']['ledger_writes'] == 0
    assert build(original, tmp_path / 'packed', reserve_bytes=0) == result
    base = tmp_path / 'packed' / result['release_id']
    region_asset = next(row for row in result['files'] if '/regions/' in row['path'])
    region = json.loads((base / region_asset['path']).read_bytes())
    assert region['partitions'][0]['transactions'] == region['partitions'][1]['transactions']
    transaction_ref = region['partitions'][0]['transactions'][0]
    body = json.loads((base / transaction_ref['url'].lstrip('/')).read_bytes())
    assert body['kind'] == 'property-transaction-pack' and body['months'][0]['deal_month'] == '202609'
    assert len(body['months'][0]['transactions']) == 4
    summary = build_summary(base / 'publication.json', tmp_path / 'summary', reserve_bytes=0)
    assert summary['audit']['source_rows'] == 4 and summary['audit']['grouped_rows'] == 3


def test_pending_month_does_not_get_a_success_reference(tmp_path):
    checkpoint = setup(tmp_path, 1)
    source = publish(checkpoint, registry(), tmp_path / 'source', reserve_bytes=0)
    original = tmp_path / 'source' / source['release_id'] / 'publication.json'
    result = build(original, tmp_path / 'packed', reserve_bytes=0)
    base = tmp_path / 'packed' / result['release_id']
    region = json.loads((base / next(row['path'] for row in result['files'] if '/regions/' in row['path'])).read_bytes())
    pending = next(row for row in region['partitions'] if row['status'] == 'pending')
    assert pending['source_rows'] is None and pending['transactions'] == []


def test_source_mutation_and_public_output_are_rejected(tmp_path):
    checkpoint = setup(tmp_path)
    source = publish(checkpoint, registry(), tmp_path / 'source', reserve_bytes=0)
    original = tmp_path / 'source' / source['release_id'] / 'publication.json'
    with pytest.raises(RealEstateError, match='public_output_forbidden'):
        build(original, tmp_path / 'public', reserve_bytes=0)
    asset = next(row for row in source['files'] if '/transactions/' in row['path'])
    (original.parent / asset['path']).write_bytes(b'changed')
    with pytest.raises(RealEstateError, match='checkpoint_size_mismatch|checkpoint_hash_mismatch'):
        build(original, tmp_path / 'packed', reserve_bytes=0)


@pytest.mark.parametrize('value', [True, 0, 17_999, 100_001])
def test_private_packing_does_not_accept_unbounded_source_file_budget(tmp_path, value):
    checkpoint = setup(tmp_path)
    with pytest.raises(RealEstateError, match='invalid_publication_file_budget'):
        publish(checkpoint, registry(), tmp_path / 'source', reserve_bytes=0, max_files=value)


def test_cached_private_input_does_not_bypass_default_publication_file_budget(tmp_path):
    checkpoint = setup(tmp_path)
    source = publish(checkpoint, registry(), tmp_path / 'source', reserve_bytes=0)
    path = tmp_path / 'source' / source['release_id'] / 'publication.json'
    source['files'] = [source['files'][0]] * 18_001
    path.write_text(json.dumps(source), encoding='utf-8')
    with pytest.raises(RealEstateError, match='publication_file_limit'):
        publish(checkpoint, registry(), tmp_path / 'source', reserve_bytes=0)


def test_two_months_share_pack_but_keep_separate_rows_and_summary_month_references(tmp_path):
    from pipeline.real_estate_fetch import Collector
    from pipeline.real_estate_summary_month_pack import build as pack_summary

    def transport(*args, **kwargs):
        value = sale() if args[1] == 'sale' else rent()
        value.update(aptSeq='11110-99999', dealYear=args[3][:4], dealMonth=str(int(args[3][4:])))
        return xml([value])

    root = tmp_path / 'checkpoint'
    collector = Collector(root, registry(), months=2, clock=lambda: STAMP, transport=transport, reserve_bytes=0)
    collector.collect(KEY, max_requests=4, min_interval=0); collector.close()
    source = publish(root, registry(), tmp_path / 'source', reserve_bytes=0)
    packed = build(tmp_path / 'source' / source['release_id'] / 'publication.json', tmp_path / 'packed', reserve_bytes=0)
    base = tmp_path / 'packed' / packed['release_id']
    region = json.loads((base / next(row['path'] for row in packed['files'] if '/regions/' in row['path'])).read_bytes())
    refs = [ref for part in region['partitions'] for ref in part['transactions']]
    assert len({ref['url'] for ref in refs}) == 1
    body = json.loads((base / refs[0]['url'].lstrip('/')).read_bytes())
    assert [month['deal_month'] for month in body['months']] == ['202608', '202609']
    assert all(len(month['transactions']) == 2 for month in body['months'])
    summary = build_summary(base / 'publication.json', tmp_path / 'summary', reserve_bytes=0)
    monthly = pack_summary(tmp_path / 'summary' / summary['summary_release_id'] / 'publication.json', tmp_path / 'monthly', reserve_bytes=0)
    summary_root = tmp_path / 'monthly' / monthly['summary_release_id']
    summary_region = json.loads((summary_root / next(row['path'] for row in monthly['files'] if '/regions/' in row['path'])).read_bytes())
    assert {ref['deal_month'] for ref in summary_region['summaries']} == {'202608', '202609'}
    assert len({ref['url'] for ref in summary_region['summaries']}) == 1
    assert monthly['audit']['summary_groups_preserved'] == 4
