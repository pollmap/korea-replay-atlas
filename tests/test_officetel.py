"""Synthetic rows based on the official OffiTrade/OffiRent Swagger field lists."""
import pytest

from pipeline.real_estate import normalize_xml_page, build_partitions, RealEstateError
from pipeline.real_estate_fetch import Collector
from pipeline.real_estate_publish import publish
from test_real_estate_fetch import xml, registry, STAMP, KEY


def row(trade='sale'):
    result = dict(sggCd='11110', sggNm='종로구', umdNm='검증동', jibun='1-2',
                  offiNm='검증오피스텔', excluUseAr='25.50', dealYear='2026',
                  dealMonth='9', dealDay='1', floor='3', buildYear='2020')
    result.update(dealAmount='12,345', cdealType='', cdealDay='') if trade == 'sale' else result.update(deposit='1,000', monthlyRent='50')
    return result


def page(trade='sale', rows=None):
    return normalize_xml_page(xml([row(trade)] if rows is None else rows), lawd_code='11110',
        deal_month='202609', retrieved_at=STAMP, trade_type=trade, property_type='officetel')


@pytest.mark.parametrize('trade,dataset', [('sale','15126464'), ('rent','15126475')])
def test_officetel_identity_money_and_source(trade, dataset):
    result = page(trade)['records'][0]
    assert result['kind'] == f'officetel-{trade}-report'
    assert result['complex_id'] is result['source_complex_id'] is result['position'] is None
    assert result['complex_name'] == '검증오피스텔'
    assert result['quality'] == 'valid' and result['issues'] == []
    assert result['area_m2'] == '25.5'
    assert result['source_fields']['offiNm'] == result['complex_name']
    assert 'aptNm' not in result['source_fields']
    assert result['provenance']['dataset_id'] == dataset
    if trade == 'sale':
        assert result['price_krw'] == 123450000
    else:
        assert (result['deposit_krw'], result['monthly_rent_krw']) == (10000000, 500000)
        assert result['cancellation']['status'] == 'not_provided'


def test_cancelled_and_scope_conflict_are_retained():
    raw = row(); raw.update(cdealType='O', cdealDay='26.09.01', sggCd='11710')
    result = page(rows=[raw])['records'][0]
    assert result['cancellation']['status'] == 'cancelled'
    assert {'field':'sggCd','code':'scope_mismatch'} in result['issues']
    assert result['quality'] == 'invalid'


def test_partitions_keep_types_and_identical_report_multiplicity_separate():
    office = page(rows=[row(), row()])
    apt = normalize_xml_page(xml([]), lawd_code='11110', deal_month='202609', retrieved_at=STAMP)
    partitions = build_partitions([office, apt])
    assert len(partitions) == 2
    result = next(p for p in partitions if p['kind'] == 'officetel-sale-report-partition')
    assert result['source']['id'] == 'molit-officetel-sale'
    assert len({r['id'] for r in result['records']}) == 2
    assert all(r['id'].startswith('molit-officetel-sale:') for r in result['records'])
    assert result['audit']['identical_row_occurrences'] == 1


def test_mislabelled_partition_is_rejected():
    office = page()
    office.pop('property_type')
    with pytest.raises(RealEstateError, match='mixed_property_record_type'):
        build_partitions([office])


def test_legacy_apartment_checkpoint_cannot_be_reused_for_officetel(tmp_path):
    c = Collector(tmp_path, registry(), months=1, clock=lambda:STAMP, reserve_bytes=0)
    with c.db:
        c.db.execute("DELETE FROM meta WHERE key='property_type'")
    before = [tuple(r) for r in c.db.execute('SELECT * FROM jobs ORDER BY id')]
    c.close()
    with pytest.raises(RealEstateError, match='property_type_changed_use_new_checkpoint'):
        Collector(tmp_path, registry(), months=1, clock=lambda:STAMP,
                  reserve_bytes=0, property_type='officetel')
    c = Collector(tmp_path, registry(), months=1, clock=lambda:STAMP, reserve_bytes=0)
    assert [tuple(r) for r in c.db.execute('SELECT * FROM jobs ORDER BY id')] == before
    c.close()


def test_checkpoint_cannot_change_property_type_and_office_cannot_publish_as_apartment(tmp_path):
    root = tmp_path/'office'
    c = Collector(root, registry(), months=1, clock=lambda:STAMP, reserve_bytes=0,
                  property_type='officetel', transport=lambda *a, **k:xml([row(a[1])]))
    try:
        c.collect(KEY, max_requests=2, min_interval=0)
        assert c.summary()['complete'] == 2
        assert c.reprocess()['coverage']['complete'] == 2
    finally:
        c.close()
    with pytest.raises(RealEstateError, match='property_type_changed_use_new_checkpoint'):
        Collector(root, registry(), months=1, clock=lambda:STAMP, reserve_bytes=0)
    with pytest.raises(RealEstateError, match='unsupported_publication_property_type'):
        publish(root, registry(), tmp_path/'candidate', reserve_bytes=0)


@pytest.mark.parametrize('kind', ['villa','house','commercial','',None])
def test_out_of_scope_types_rejected_before_checkpoint_creation(tmp_path, kind):
    root = tmp_path/'no-create'
    with pytest.raises(RealEstateError, match='unsupported_property_type'):
        Collector(root, registry(), property_type=kind, reserve_bytes=0)
    assert not root.exists()
