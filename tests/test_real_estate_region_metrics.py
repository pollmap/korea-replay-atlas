from pipeline.real_estate_region_metrics import observations, aggregate, months

def row(identity='one',price=840_000_000,area='84',eligible=True,trade='sale',**kw):
    return {'id':identity,'statistics_eligible':eligible,'area_m2':area,'trade_type':trade,'price_krw':price,**kw}

def test_default_band_is_exclusive_and_counts_distinct_repeated_reports():
    groups=observations([row(),row('two'),row('small',area='83.99'),row('large',area='85'),row('cancel',eligible=False)])
    assert len(groups[('84-band','sale')])==2
    assert len(groups[('','sale')])==4

def test_period_median_is_computed_from_reports_not_monthly_medians():
    by={'202608':observations([row(price=84),row('two',price=84)]),'202609':observations([row(price=8400)])}
    result=aggregate(by,{('202607','sale'):'empty',('202608','sale'):'complete',('202609','sale'):'complete'},'202609',3,'84-band','sale')
    assert result=={'status':'complete','count':3,'median_per_m2':1,'covered':3,'expected':3,'unavailable':0}

def test_partial_counts_and_zero_are_not_reported_as_complete():
    value=aggregate({}, {('202609','sale'):'empty'},'202609',3,'84-band','sale')
    assert value['status']=='partial' and value['count']==0 and value['median_per_m2'] is None
    value=aggregate({}, {},'202609',3,'84-band','sale')
    assert value['status']=='pending' and value['count'] is None
    value=aggregate({}, {('202609','sale'):'empty'},'202609',1,'84-band','sale')
    assert value['status']=='complete' and value['count']==0

def test_source_unavailable_and_failed_are_distinct():
    assert aggregate({}, {('202609','rent'):'source_unavailable'},'202609',1,'','jeonse')['status']=='source_unavailable'
    assert aggregate({}, {('202609','sale'):'failed'},'202609',1,'','sale')['status']=='failed'

def test_rent_filters_do_not_mix_monthly_and_jeonse():
    values=observations([row(trade='rent',deposit_krw=420_000_000,monthly_rent_krw=0),row('two',trade='rent',deposit_krw=84_000_000,monthly_rent_krw=1_000_000),row('unknown',trade='rent',deposit_krw=None,monthly_rent_krw=None)])
    assert values[('84-band','jeonse')]==[5_000_000]
    assert values[('84-band','monthly')]==[1_000_000]
    assert len(values[('84-band','rent')])==2

def test_month_window_includes_exactly_240_completed_months():
    value=months('202609',240)
    assert (len(value),value[0],value[-1])==(240,'200610','202609')