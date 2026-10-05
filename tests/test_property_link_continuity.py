from pipeline.property_link_continuity import stable_identity

def test_existing_join_requires_same_source_id_and_legal_address():
    prior={'id':'molit-apt:11710:11710-8865','source_complex_id':'11710-8865','lawd_code':'11710',
        'name':'헬리오시티','legal_dong_code':'1171010700','legal_dong_name':'가락동','lot_number':'913','address_conflict':False}
    assert stable_identity(prior,{**prior,'name':'헬리오 시티','observed_name_variants':['헬리오시티']})
    for update in ({'lot_number':'914'},{'legal_dong_code':'1171010100'},{'address_conflict':True},
        {'source_complex_id':'other'},{'name':'다른단지'},{'lot_number':None}):
        assert not stable_identity(prior,{**prior,**update})
