import json

import pytest

from pipeline.real_estate import RealEstateError
from pipeline.real_estate_complex_summary import build as summarize
from pipeline.real_estate_publish import publish
from pipeline.real_estate_summary_month_pack import build
from test_real_estate_fetch import registry
from test_real_estate_publish import setup


def test_nondefault_byte_budget_creates_distinct_immutable_summary(tmp_path):
    root = setup(tmp_path)
    source = publish(root, registry(), tmp_path / 'source', reserve_bytes=0)
    summary = summarize(tmp_path / 'source' / source['release_id'] / 'publication.json', tmp_path / 'summary', reserve_bytes=0)
    input_path = tmp_path / 'summary' / summary['summary_release_id'] / 'publication.json'
    normal = build(input_path, tmp_path / 'packed', reserve_bytes=0)
    smaller = build(input_path, tmp_path / 'packed', reserve_bytes=0, target_bytes=3 * 1024**2)
    assert smaller['summary_release_id'] != normal['summary_release_id']
    assert smaller['audit']['summary_group_identity_sha256'] == normal['audit']['summary_group_identity_sha256']


def test_month_pack_preserves_summary_counts_and_complex_lookup(tmp_path):
    root = setup(tmp_path); source = publish(root, registry(), tmp_path / 'source', reserve_bytes=0)
    summary = summarize(tmp_path / 'source' / source['release_id'] / 'publication.json', tmp_path / 'summary', reserve_bytes=0)
    input_path = tmp_path / 'summary' / summary['summary_release_id'] / 'publication.json'
    result = build(input_path, tmp_path / 'packed', reserve_bytes=0)
    assert result['summary_release_id'] != summary['summary_release_id']
    assert result['property_release_id'] == summary['property_release_id']
    assert result['audit']['grouped_rows'] == 3
    assert result['audit']['summary_groups_preserved'] == 2
    assert build(input_path, tmp_path / 'packed', reserve_bytes=0) == result
    base = tmp_path / 'packed' / result['summary_release_id']
    region = json.loads((base / next(row['path'] for row in result['files'] if '/regions/' in row['path'])).read_bytes())
    month_ref = region['summaries'][0]
    blob = json.loads((base / month_ref['url'].lstrip('/')).read_bytes())
    assert blob['kind'] == 'property-complex-summary-month-pack'
    assert blob['months'][0]['deal_month'] == month_ref['deal_month']
    refs = region['complex_chunks']['molit-apt:11110:11110-99999']
    complex_blob = json.loads((base / refs[0]['url'].lstrip('/')).read_bytes())
    assert complex_blob['summary_release_id'] == result['summary_release_id']
    assert complex_blob['rows'] == blob['months'][0]['rows']


def test_changed_month_input_and_public_output_are_rejected(tmp_path):
    root = setup(tmp_path); source = publish(root, registry(), tmp_path / 'source', reserve_bytes=0)
    summary = summarize(tmp_path / 'source' / source['release_id'] / 'publication.json', tmp_path / 'summary', reserve_bytes=0)
    input_path = tmp_path / 'summary' / summary['summary_release_id'] / 'publication.json'
    with pytest.raises(RealEstateError, match='public_output_forbidden'): build(input_path, tmp_path / 'public', reserve_bytes=0)
    asset = next(row for row in summary['files'] if '/rows/' in row['path'])
    (input_path.parent / asset['path']).write_bytes(b'changed')
    with pytest.raises(RealEstateError, match='checkpoint_size_mismatch|checkpoint_hash_mismatch'):
        build(input_path, tmp_path / 'packed', reserve_bytes=0)


def test_compressed_input_and_output_preserve_summary_identity_and_decode_bounds(tmp_path):
    from pipeline.real_estate_publish import checked_read
    root=setup(tmp_path);pub=publish(root,registry(),tmp_path/'source',reserve_bytes=0,packed_transactions=True,compressed_transactions=True)
    summary=summarize(tmp_path/'source'/pub['release_id']/'publication.json',tmp_path/'summary',reserve_bytes=0,compressed_assets=True)
    assert any('transport' in row for row in summary['files'])
    source=tmp_path/'summary'/summary['summary_release_id']/'publication.json'
    packed=build(source,tmp_path/'packed',reserve_bytes=0,compressed_assets=True)
    plain=build(source,tmp_path/'packed',reserve_bytes=0)
    assert packed['summary_release_id']!=plain['summary_release_id']
    assert packed['audit']['grouped_rows']==plain['audit']['grouped_rows']==3
    assert packed['audit']['summary_group_identity_sha256']==plain['audit']['summary_group_identity_sha256']
    assert build(source,tmp_path/'packed',reserve_bytes=0,compressed_assets=True)==packed
    for row in packed['files']:
        decoded=checked_read(tmp_path/'packed'/packed['summary_release_id'],{**row,'bytes':row['byte_length']},24*1024**2)
        assert json.loads(decoded)['property_release_id']==pub['release_id']
    bad=next(row for row in packed['files'] if 'transport' in row)
    target=tmp_path/'packed'/packed['summary_release_id']/bad['path'];target.write_bytes(target.read_bytes()[:-8])
    with pytest.raises(RealEstateError):build(source,tmp_path/'packed',reserve_bytes=0,compressed_assets=True)
