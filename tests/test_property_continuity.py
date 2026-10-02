import json
import pytest
from pipeline.real_estate import RealEstateError
from pipeline.real_estate_fetch import Collector
from pipeline.real_estate_publish import publish
from pipeline.real_estate_transaction_pack import build as pack
from pipeline.property_continuity import compare, _region
from test_real_estate_publish import setup, sale
from test_real_estate_fetch import registry, xml, rent, STAMP, KEY


def published(tmp, limit=2):
    tmp.mkdir()
    source = setup(tmp, limit)
    result = publish(source, registry(), tmp/'published', reserve_bytes=0)
    return tmp/'published'/result['release_id']/'publication.json'


def test_raw_to_packed_preserves_all_ids_and_duplicate_occurrences(tmp_path):
    previous = published(tmp_path/'old')
    packed = pack(previous, tmp_path/'packed', reserve_bytes=0)
    report = compare(previous, tmp_path/'packed'/packed['release_id']/'publication.json')
    assert report['old_rows'] == report['new_rows'] == report['retained_ids'] == 4
    assert report['removed_ids'] == report['added_ids'] == 0
    assert report['automatic_transition_eligible'] and report['checked_all_previous_ids']
    assert not report['requires_source_revision_review']
    assert report['public_release'] is False
    assert report['source_calls'] == report['ledger_writes'] == 0
    assert len(report['previous_publication_sha256']) == 64


def test_new_rows_do_not_hide_lost_completed_partition(tmp_path):
    previous = published(tmp_path/'old')
    partial = published(tmp_path/'partial', 1)
    report = compare(previous, partial)
    assert report['old_rows'] == 4 and report['new_rows'] == 2
    assert report['removed_ids'] == 2
    assert not report['automatic_transition_eligible']
    assert report['blockers'][0]['reason'] == 'completed_partition_lost'
    added = compare(partial, previous)
    assert added['added_ids'] == 2 and added['removed_ids'] == 0
    assert added['automatic_transition_eligible']


def revised(tmp, stamp, amount='25,000'):
    def transport(*args, **kwargs):
        rows = [sale(), sale(True)] if args[1] == 'sale' else [rent(), rent()]
        if args[1] == 'sale':
            for row in rows: row['dealAmount'] = amount
        else:
            for row in rows: row['aptSeq'] = '11110-99999'
        return xml(rows)
    root = tmp/'checkpoint'
    collector = Collector(root, registry(), months=1, clock=lambda:stamp, transport=transport, reserve_bytes=0)
    collector.collect(KEY,max_requests=2,min_interval=0);collector.close()
    result = publish(root, registry(), tmp/'published', reserve_bytes=0)
    return tmp/'published'/result['release_id']/'publication.json'


def test_newer_source_revisions_require_review_and_never_auto_promote(tmp_path):
    previous = published(tmp_path/'old')
    candidate = revised(tmp_path/'new', '2026-09-30T12:00:00Z')
    report = compare(previous, candidate)
    assert report['removed_ids'] == 2 and report['added_ids'] == 2
    assert not report['blockers']
    assert report['requires_source_revision_review']
    assert not report['automatic_transition_eligible']
    assert report['changed_jobs'][0]['requires_source_revision_review']


def test_same_timestamp_changed_source_is_not_accepted_as_revision(tmp_path):
    previous = published(tmp_path/'old')
    candidate = revised(tmp_path/'new', STAMP)
    report = compare(previous, candidate)
    assert report['removed_ids'] == 2
    assert report['blockers'][0]['reason'] == 'missing_without_newer_snapshot'


def test_older_snapshot_blocked_even_when_ids_unchanged(tmp_path):
    previous = revised(tmp_path/'old', '2026-09-30T12:00:00Z', '20,000')
    candidate = revised(tmp_path/'new', STAMP, '20,000')
    report = compare(previous, candidate)
    assert report['removed_ids'] == 0
    assert {x['reason'] for x in report['blockers']} == {'older_snapshot'}
    assert not report['automatic_transition_eligible']


def test_corrupt_payload_fails_hash_verification(tmp_path):
    previous = published(tmp_path/'old')
    receipt = json.loads(previous.read_bytes())
    asset = next(x for x in receipt['files'] if '/transactions/' in x['path'])
    (previous.parent/asset['path']).write_bytes(b'corrupt')
    with pytest.raises(RealEstateError, match='checkpoint_size_mismatch|checkpoint_hash_mismatch'):
        compare(previous, previous)


def test_duplicate_and_scope_errors_cannot_pass_region_audit():
    ref={'lawd_code':'11110','index':{'url':'index'}}
    part={'deal_month':'202609','trade_type':'sale','source_rows':2,'transactions':[{'url':'rows'}]}
    row={'id':'a','lawd_code':'11110','contract_date':'2026-09-01','trade_type':'sale','source_input_sha256':'a'*64}
    documents={'index':{'lawd_code':'11110','partitions':[part]},'rows':{'lawd_code':'11110','kind':'property-transactions','deal_month':'202609','transactions':[row,row]}}
    with pytest.raises(RealEstateError,match='continuity_duplicate_identity'):
        _region(documents.__getitem__,ref)
    documents['rows']['transactions']=[{**row,'lawd_code':'99999'}]
    with pytest.raises(RealEstateError,match='continuity_row_scope'):
        _region(documents.__getitem__,ref)
    documents['rows']['transactions']=[row]
    with pytest.raises(RealEstateError,match='continuity_partition_count'):
        _region(documents.__getitem__,ref)


def test_report_cannot_overwrite_input_publication(tmp_path, monkeypatch):
    from pipeline.property_continuity import main
    previous = published(tmp_path/'old')
    original = previous.read_bytes()
    monkeypatch.setattr('sys.argv', ['continuity', '--previous', str(previous), '--candidate', str(previous), '--report', str(previous)])
    with pytest.raises(SystemExit) as result:
        main()
    assert result.value.code == 1
    assert previous.read_bytes() == original
