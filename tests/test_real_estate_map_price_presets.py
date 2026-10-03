from pipeline.real_estate_map_price_presets import choose

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