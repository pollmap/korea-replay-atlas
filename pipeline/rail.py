"""Official station events; never turn station timestamps into invented train GPS."""
import argparse
import json
import time
import xml.etree.ElementTree as ET
from datetime import datetime,timezone,timedelta
import requests
from .core import LOCAL,PUBLIC,atomic_json,digest,now,publish_file
from .elevation import sample_heights

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
    raw=collect_sample(day);meta=json.loads(raw.with_suffix('.meta.json').read_text(encoding='utf-8'))
    osm=LOCAL/'raw'/'osm'/'daejeon-station.osm'
    if not osm.exists():raise ValueError('OSM station source must be downloaded first')
    station=None
    for node in ET.parse(osm).getroot().findall('node'):
        tags={t.attrib['k']:t.attrib['v'] for t in node.findall('tag')}
        if tags.get('railway')=='station' and tags.get('station')=='train' and tags.get('name')=='대전':station=node
    if station is None:raise ValueError('Unique national rail station coordinate not found')
    lon,lat=float(station.attrib['lon']),float(station.attrib['lat']);height=float(sample_heights([lon],[lat])[0])
    tracks=[];rejected=[]
    for row in json.loads(raw.read_text(encoding='utf-8'))['records']:
        try:
            arrival=parse_korail_time(row.get('trn_arvl_dt'));departure=parse_korail_time(row.get('trn_dptre_dt'))
            if not arrival or not departure or departure<arrival:raise ValueError('Invalid arrival/departure pair')
            duration=(datetime.fromisoformat(departure)-datetime.fromisoformat(arrival)).total_seconds()
            if duration>7200:raise ValueError('Station dwell longer than validation limit')
        except (ValueError,TypeError) as error:rejected.append({'train':row.get('trn_no'),'reason':str(error)});continue
        track_id=f'{day}:{row["trn_no"]}:{row["trn_run_sn"]}'
        tracks.append({'id':track_id,'label':f'{row["trn_no"]} 열차 · 대전역 정차','layer':'rail','position_evidence':'calculation','max_gap_seconds':max(1,duration),
          'description':'공식 도착·출발 기록에 해당하는 정차 시간입니다. 역 대표 좌표에 표시하며 승강장·GPS 이동 위치가 아닙니다.',
          'provenance':{'source_id':'korail','source_record_id':track_id,'dataset_version':day,'observed_at':arrival,'retrieved_at':meta['retrieved_at'],'evidence_type':'official_record','input_hash':meta['sha256'],'transform_version':'station-events-v1'},
          'coordinate_provenance':{'source_id':'osm','record_id':station.attrib['id'],'input_hash':digest(osm)},
          'points':[{'time':arrival,'lon':lon,'lat':lat,'height':height},{'time':departure,'lon':lon,'lat':lat,'height':height}]})
    body={'schema_version':1,'tracks':tracks};fingerprint=__import__('hashlib').sha256(json.dumps(body,sort_keys=True).encode()).hexdigest()[:12]
    target=PUBLIC/'replay'/'korail'/f'daejeon-{day}-{fingerprint}.json';atomic_json(target,body)
    if tracks:publish_file(target,asset_id=f'korail-daejeon-{day}',layer='rail',format='replay',bbox=[lon-.001,lat-.001,lon+.001,lat+.001],source_id='korail',version=day,count=len(tracks),**{'from':min(t['points'][0]['time'] for t in tracks),'to':max(t['points'][-1]['time'] for t in tracks)})
    audit={'source_rows':meta['count'],'station_events':len(tracks),'rejected':rejected,'coordinate_source':'OSM node '+station.attrib['id'],'scope':'station dwell only; no between-station motion'}
    atomic_json(LOCAL/'audit'/f'korail-daejeon-{day}.json',audit);print(json.dumps(audit,ensure_ascii=False))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--day',required=True);args=parser.parse_args();daejeon_replay(args.day)
