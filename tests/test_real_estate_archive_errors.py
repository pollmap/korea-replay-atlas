import json
import pytest
from pipeline.real_estate import RealEstateError
from pipeline.real_estate_archive import D1Archive


@pytest.mark.parametrize('upstream,expected', [
    ({'code':7500,'message':"Your account has exceeded D1's free tier daily row write limit. Upgrade to a paid plan or wait until tomorrow (midnight UTC)."},'archive_daily_write_limit'),
    ({'code':7500,'message':'unrelated SQL failure private-fixture'},'archive_remote_http_400'),
    ({'code':7500,'message':'collection_daily_budget: fixture'},'collection_daily_budget'),
])
def test_remote_limits_remain_distinct_and_do_not_retry_or_expose_messages(monkeypatch, upstream, expected):
    calls=[]
    class Connection:
        status=400
        def __init__(self,*args,**kwargs):pass
        def request(self,*args,**kwargs):calls.append(True)
        def getresponse(self):return self
        def read(self,*args):return json.dumps({'success':False,'errors':[upstream]}).encode()
        def close(self):pass
    monkeypatch.setattr('pipeline.real_estate_archive.http.client.HTTPSConnection',Connection)
    store=D1Archive({'account_id':'a'*32,'control_database':'0'*36,'object_databases':[str(i)*36 for i in range(1,5)]},token='private-fixture')
    with pytest.raises(RealEstateError) as caught:
        store.query(store.control,'SELECT 1')
    assert caught.value.code==expected
    assert 'private-fixture' not in str(caught.value)
    assert len(calls)==1
