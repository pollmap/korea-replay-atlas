"""Official station events; never turn station timestamps into invented train GPS."""
import time
from datetime import datetime,timezone,timedelta
import requests
from .core import LOCAL,atomic_json,digest,now

KST=timezone(timedelta(hours=9))
def parse_korail_time(value):
    if not value or str(value).strip() in ('','null'):return None
    date=datetime.fromisoformat(str(value).strip())
    if date.tzinfo is None:date=date.replace(tzinfo=KST)
    return date.astimezone(timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')

def collect_sample(day,station='대전'):
    """A bounded, manual validation snapshot from the provider's public data viewer.

    Scheduled/nationwide collection must use the issued service key and main API.
    """
    datetime.strptime(day,'%Y%m%d')
    target=LOCAL/'raw'/'korail'/f'{day}-{station}.json'
    if target.exists():return target
    records=[];pages=[]
    endpoint='https://openapis.korail.com/samples/public/call/run/travelerTrainRunInfo'
    for page in range(1,11):
        response=requests.get(endpoint,params={'pageNo':page,'numOfRows':100,'cond[run_ymd::GTE]':day,'cond[run_ymd::LTE]':day,'cond[stn_nm::EQ]':station},timeout=40)
        response.raise_for_status();body=response.json()['response']
        if str(body['header']['resultCode']) not in ('0','00'):raise ValueError('Korail source returned an error')
        rows=body['body']['items']['item'];rows=rows if isinstance(rows,list) else [rows]
        records.extend(rows);pages.append({'page':page,'body':body,'retrieved_at':now()})
        total=int(body['body']['totalCount'])
        if total>1000:raise ValueError('Public viewer snapshot limit exceeded; use issued API key')
        if len(records)>=total:break
        if not rows:raise ValueError('Korail pagination ended prematurely')
        time.sleep(.5)
    if len(records)!=total:raise ValueError('Korail total count mismatch')
    atomic_json(target,{'records':records,'pages':pages})
    atomic_json(target.with_suffix('.meta.json'),{'source_url':endpoint,'dataset_version':day,'retrieved_at':now(),'sha256':digest(target),'count':len(records),'scope':'one-station manual public-viewer validation snapshot'})
    return target

def daejeon_replay(day):
    from .retired_3d import retired
    retired()

if __name__ == '__main__':
    from .retired_3d import retired
    retired()
