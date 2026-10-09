from pipeline.real_estate_map_price_presets import choose
import json
import pytest
from pipeline.real_estate import RealEstateError
from pipeline.real_estate_map_price_presets import build
from pipeline.real_estate_publish import publish
from pipeline.real_estate_complex_summary import build as summarize
from test_real_estate_fetch import registry
from test_real_estate_publish import setup

def row(identity='a',area='84.99',month='202609',date='2026-09-01',kind='sale',tid='a'):
 return {'complex_id':identity,'area_m2':area,'deal_month':month,'trade_type':'sale' if kind=='sale' else 'rent','rent_kind':kind,'latest_contract_date':date,'latest_transaction_id':tid}

def test_filter_is_applied_before_selecting_latest_report():
 values=[row(date='2026-09-20',area='59'),row(date='2026-09-10'),row(month='202608',date='2026-08-31')]
 assert choose(values,{'202609'},'84-band','sale')==[values[1]]

def test_price_choice_is_stable_for_tied_dates_and_retains_independent_ids():
 values=[row(tid='b'),row(tid='a'),row(identity='second')]
 assert choose(values,{'202609'},'','sale')==[values[1],values[2]]

def test_rent_types_and_missing_month_are_not_substituted():
 values=[row(kind='jeonse'),row(kind='monthly',date='2026-09-20')]
 assert choose(values,{'202609'},'84-band','jeonse')==[values[0]]
 assert choose(values,{'202609'},'84-band','rent')==[values[1]]
 assert choose(values,{'202608'},'84-band','sale')==[]

def compressed_source(tmp_path):
 root=setup(tmp_path)
 property=publish(root,registry(),tmp_path/'transactions',reserve_bytes=0,packed_transactions=True,compressed_transactions=True)
 summary=summarize(tmp_path/'transactions'/property['release_id']/'publication.json',tmp_path/'summaries',reserve_bytes=0,compressed_assets=True)
 source=tmp_path/'summaries'/summary['summary_release_id']/'publication.json'
 nav=tmp_path/'navigation.json'
 nav.write_text(json.dumps({'property_release_id':property['release_id'],'points':[['molit-apt:11110:11110-99999']]}),encoding='utf-8')
 return source,nav,summary

def test_compressed_current_release_uses_exact_complex_packs_and_keeps_cancellation_and_rent_types(tmp_path):
 source,nav,summary=compressed_source(tmp_path)
 # Regional month bodies are unnecessary for the linked map point. The exact
 # per-complex packs remain intact and independently hash-checked.
 monthly=next(f for f in summary['files'] if '/rows/' in f['path'])
 (source.parent/monthly['path']).write_bytes(b'unused monthly cache')
 output=tmp_path/'presets';build(source,nav,output)
 value=json.loads(next(output.glob('property-map-price-*.json')).read_bytes())
 assert value['property_release_id']==summary['property_release_id']
 selected=value['views']['202609|36|84-band|sale']
 assert len(selected)==1 and value['rows'][selected[0]]['transaction_count']==1
 assert value['rows'][selected[0]]['latest_price_krw']==200_000_000
 rent=value['views']['202609|36|84-band|rent']
 assert len(rent)==1 and value['rows'][rent[0]]['transaction_count']==2
 assert value['views']['202609|36|84-band|monthly']==[]
 before=next(output.glob('*.json')).read_bytes();build(source,nav,output)
 assert next(output.glob('*.json')).read_bytes()==before

def test_selected_compressed_summary_corruption_does_not_generate_a_preset(tmp_path):
 source,nav,summary=compressed_source(tmp_path)
 selected=next(f for f in summary['files'] if '/complexes/' in f['path'])
 (source.parent/selected['path']).write_bytes(b'broken')
 with pytest.raises(RealEstateError,match='checkpoint_size_mismatch|checkpoint_hash_mismatch'):
  build(source,nav,tmp_path/'presets')
 assert not list((tmp_path/'presets').glob('*.json'))
